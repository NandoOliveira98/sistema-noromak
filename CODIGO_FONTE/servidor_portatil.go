package main

import (
	"archive/zip"
	"bytes"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	appMarker      = "noromak_portatil_rede"
	appVersion     = "2026.08.03.12"
	maxBody        = 100 * 1024 * 1024
	firstPort      = 8875
	lastPort       = 8894
	staleWriteLock = 2 * time.Minute
	staleOrderLock = 5 * time.Minute
	readRetryCount = 10
	readRetryWait  = 35 * time.Millisecond
)

var (
	rootDir   string
	appDir    string
	dataDir   string
	backupDir string
	logDir    string
	serverMu  sync.Mutex
	srv       *http.Server
)

type envelope struct {
	Value     string `json:"value"`
	Version   int    `json:"version"`
	UpdatedAt string `json:"updated_at"`
}

type orderFile struct {
	Pedido     string           `json:"pedido"`
	OrderIndex int              `json:"order_index"`
	UpdatedAt  string           `json:"updated_at"`
	Items      []map[string]any `json:"items"`
}

type lockInfo struct {
	Pedido     string `json:"pedido"`
	ClientID   string `json:"clientId"`
	Operador   string `json:"operador"`
	Computador string `json:"computador"`
	Atualizado string `json:"atualizado"`
	Iniciado   string `json:"iniciado"`
}

func nowISO() string { return time.Now().Format(time.RFC3339) }

func normalize(v any) string {
	s := strings.ToUpper(strings.TrimSpace(fmt.Sprint(v)))
	return strings.Join(strings.Fields(s), "")
}

func safeName(s string) string {
	n := normalize(s)
	var b strings.Builder
	for _, r := range n {
		if (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		b.WriteString("SEM_ID")
	}
	h := sha1.Sum([]byte(s))
	return b.String() + "_" + hex.EncodeToString(h[:4])
}

func keyPath(key string) string {
	return filepath.Join(dataDir, "BANCO", safeName(key)+".json")
}

func writeLockPath(key string) string {
	return filepath.Join(dataDir, "BLOQUEIOS", "gravacao_"+safeName(key)+".lck")
}

func acquireFileLock(path string, timeout time.Duration) (func(), error) {
	deadline := time.Now().Add(timeout)
	for {
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0666)
		if err == nil {
			_, _ = fmt.Fprintf(f, "%s\n%d\n", nowISO(), os.Getpid())
			_ = f.Close()
			return func() { _ = os.Remove(path) }, nil
		}
		if !os.IsExist(err) {
			return nil, err
		}
		if st, statErr := os.Stat(path); statErr == nil && time.Since(st.ModTime()) > staleWriteLock {
			_ = os.Remove(path)
			continue
		}
		if time.Now().After(deadline) {
			return nil, fmt.Errorf("o arquivo compartilhado está ocupado por outra máquina")
		}
		time.Sleep(80 * time.Millisecond)
	}
}

func atomicWrite(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0777); err != nil {
		return err
	}
	tmp := path + fmt.Sprintf(".tmp.%d.%d", os.Getpid(), time.Now().UnixNano())
	if err := os.WriteFile(tmp, data, 0666); err != nil {
		return err
	}
	// Em unidades SMB, Rename sobre arquivo existente pode falhar no Windows.
	_ = os.Remove(path)
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func readFileRetry(path string) ([]byte, error) {
	var last error
	for i := 0; i < readRetryCount; i++ {
		b, err := os.ReadFile(path)
		if err == nil {
			return b, nil
		}
		last = err
		if !os.IsNotExist(err) {
			return nil, err
		}
		time.Sleep(readRetryWait)
	}
	return nil, last
}

func itemID(key string, item any, index int) string {
	m, ok := item.(map[string]any)
	if !ok {
		b, _ := json.Marshal(item)
		h := sha1.Sum(b)
		return "primitive:" + hex.EncodeToString(h[:])
	}
	fields := []string{}
	switch key {
	case "conf_pedidos", "conf_pedidos_rascunhos":
		fields = []string{"__uidLinha", "__uidPedido"}
	case "conf_produtos":
		fields = []string{"__uidProduto", "SKU", "Código de Barras"}
	case "conf_usuarios":
		fields = []string{"__uidUsuario", "Login", "login", "Email"}
	case "conf_solicitacoes_usuarios":
		fields = []string{"id", "__uidSolicitacao", "Login", "login"}
	case "conf_historico_conferencias_v2", "conf_logs":
		fields = []string{"id", "__uid", "uid", "dataHora", "data"}
	}
	for _, f := range fields {
		if v, exists := m[f]; exists && strings.TrimSpace(fmt.Sprint(v)) != "" {
			return f + ":" + normalize(v)
		}
	}
	if key == "conf_pedidos" || key == "conf_pedidos_rascunhos" {
		parts := []string{normalize(m["Pedido"]), normalize(m["SKU"]), normalize(m["Código de Barras"]), normalize(m["Descrição"])}
		composite := strings.Join(parts, "|")
		if strings.Trim(composite, "|") != "" {
			return "pedido:" + composite
		}
	}
	b, _ := json.Marshal(m)
	h := sha1.Sum(b)
	return "hash:" + hex.EncodeToString(h[:])
}

func mergeArrays(key string, current, old, newer []any) []any {
	currentIDs := make([]string, len(current))
	currentMap := map[string]any{}
	for i, item := range current {
		id := itemID(key, item, i)
		currentIDs[i] = id
		currentMap[id] = item
	}
	oldMap := map[string]any{}
	for i, item := range old {
		oldMap[itemID(key, item, i)] = item
	}
	newMap := map[string]any{}
	for i, item := range newer {
		newMap[itemID(key, item, i)] = item
	}
	for id := range oldMap {
		if _, exists := newMap[id]; !exists {
			delete(currentMap, id)
		}
	}
	changed := []string{}
	for i, item := range newer {
		id := itemID(key, item, i)
		ob, _ := json.Marshal(oldMap[id])
		nb, _ := json.Marshal(item)
		if _, exists := oldMap[id]; !exists || !bytes.Equal(ob, nb) {
			currentMap[id] = item
			changed = append(changed, id)
		}
	}
	result := []any{}
	used := map[string]bool{}
	appendID := func(id string) {
		if item, ok := currentMap[id]; ok && !used[id] {
			result = append(result, item)
			used[id] = true
		}
	}
	for _, id := range currentIDs {
		appendID(id)
	}
	for _, id := range changed {
		appendID(id)
	}
	ids := make([]string, 0, len(currentMap))
	for id := range currentMap {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		appendID(id)
	}
	return result
}

func mergeValue(key, currentText, oldText, newText string, currentExists bool, force bool) string {
	if force || !currentExists || currentText == oldText {
		return newText
	}
	var current, old, newer []any
	if json.Unmarshal([]byte(currentText), &current) == nil && json.Unmarshal([]byte(oldText), &old) == nil && json.Unmarshal([]byte(newText), &newer) == nil {
		merged := mergeArrays(key, current, old, newer)
		b, _ := json.Marshal(merged)
		return string(b)
	}
	return newText
}

func getField(m map[string]any, names ...string) any {
	for _, n := range names {
		if v, ok := m[n]; ok {
			return v
		}
	}
	// comparação sem acentos não é necessária para os campos usados aqui.
	return nil
}

func loadOrders() (string, int, string, bool, error) {
	dir := filepath.Join(dataDir, "PEDIDOS")
	entries, err := os.ReadDir(dir)
	if err != nil && !os.IsNotExist(err) {
		return "", 0, "", false, err
	}
	files := []orderFile{}
	newest := ""
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
			continue
		}
		b, err := readFileRetry(filepath.Join(dir, e.Name()))
		if err != nil {
			continue
		}
		var f orderFile
		if json.Unmarshal(b, &f) == nil {
			if isOrderCompleted(f.Pedido) {
				archiveOrder(filepath.Join(dir, e.Name()), f, true)
				_ = os.Remove(filepath.Join(dir, e.Name()))
				continue
			}
			files = append(files, f)
			if f.UpdatedAt > newest {
				newest = f.UpdatedAt
			}
		}
	}
	sort.SliceStable(files, func(i, j int) bool {
		if files[i].OrderIndex == files[j].OrderIndex {
			return files[i].Pedido < files[j].Pedido
		}
		return files[i].OrderIndex < files[j].OrderIndex
	})
	all := []map[string]any{}
	for _, f := range files {
		all = append(all, f.Items...)
	}
	b, _ := json.Marshal(all)
	ver := len(files)
	return string(b), ver, newest, len(files) > 0, nil
}

func completedMarkerPath(pedido string) string {
	return filepath.Join(dataDir, "CONCLUIDOS", "INDICE", safeName(pedido)+".done")
}

func isOrderCompleted(pedido string) bool {
	if strings.TrimSpace(pedido) == "" {
		return false
	}
	_, err := os.Stat(completedMarkerPath(pedido))
	return err == nil
}

func markOrderCompleted(pedido string) error {
	if strings.TrimSpace(pedido) == "" {
		return errors.New("pedido não informado para marcação de conclusão")
	}
	return atomicWrite(completedMarkerPath(pedido), []byte(nowISO()+"\n"))
}

func filterCompletedOrders(value string) (string, error) {
	var arr []map[string]any
	if err := json.Unmarshal([]byte(value), &arr); err != nil {
		return "", fmt.Errorf("pedidos inválidos: %w", err)
	}
	filtered := make([]map[string]any, 0, len(arr))
	for _, item := range arr {
		pedido := strings.TrimSpace(fmt.Sprint(getField(item, "Pedido")))
		if pedido == "" || isOrderCompleted(pedido) {
			continue
		}
		filtered = append(filtered, item)
	}
	b, err := json.Marshal(filtered)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

func completed(items []map[string]any) bool {
	if len(items) == 0 {
		return false
	}
	for _, it := range items {
		q, _ := strconv.ParseFloat(fmt.Sprint(getField(it, "Quantidade")), 64)
		l, _ := strconv.ParseFloat(fmt.Sprint(getField(it, "Lido")), 64)
		if q <= 0 || l < q {
			return false
		}
	}
	return true
}

func archiveOrder(filePath string, f orderFile, completedOrder bool) {
	folder := "REMOVIDOS"
	if completedOrder {
		folder = "CONCLUIDOS"
	}
	month := time.Now().Format("2006-01")
	targetDir := filepath.Join(dataDir, folder, month)
	_ = os.MkdirAll(targetDir, 0777)
	suffix := time.Now().Format("20060102_150405.000")
	target := filepath.Join(targetDir, safeName(f.Pedido)+"_"+suffix+".json")
	if b, err := os.ReadFile(filePath); err == nil {
		_ = atomicWrite(target, b)
	}
}

func saveOrders(value string) error {
	var arr []map[string]any
	if err := json.Unmarshal([]byte(value), &arr); err != nil {
		return fmt.Errorf("pedidos inválidos: %w", err)
	}
	dir := filepath.Join(dataDir, "PEDIDOS")
	if err := os.MkdirAll(dir, 0777); err != nil {
		return err
	}

	existing := map[string]struct {
		path string
		file orderFile
	}{}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
			continue
		}
		p := filepath.Join(dir, e.Name())
		b, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		var f orderFile
		if json.Unmarshal(b, &f) == nil {
			existing[normalize(f.Pedido)] = struct {
				path string
				file orderFile
			}{p, f}
		}
	}

	groups := map[string]*orderFile{}
	orderKeys := []string{}
	for _, item := range arr {
		pedido := strings.TrimSpace(fmt.Sprint(getField(item, "Pedido")))
		if pedido == "" || isOrderCompleted(pedido) {
			continue
		}
		k := normalize(pedido)
		g, ok := groups[k]
		if !ok {
			g = &orderFile{Pedido: pedido, OrderIndex: len(orderKeys), UpdatedAt: nowISO(), Items: []map[string]any{}}
			groups[k] = g
			orderKeys = append(orderKeys, k)
		}
		g.Items = append(g.Items, item)
	}

	for k, old := range existing {
		if _, remains := groups[k]; !remains {
			archiveOrder(old.path, old.file, completed(old.file.Items))
			_ = os.Remove(old.path)
		}
	}
	for _, k := range orderKeys {
		g := groups[k]
		b, _ := json.MarshalIndent(g, "", "  ")
		path := filepath.Join(dir, safeName(g.Pedido)+".json")
		if err := atomicWrite(path, b); err != nil {
			return err
		}
		// elimina eventual nome antigo do mesmo pedido
		if old, ok := existing[k]; ok && old.path != path {
			_ = os.Remove(old.path)
		}
	}
	return nil
}

func loadGeneric(key string) (string, int, string, bool, error) {
	path := keyPath(key)
	b, err := readFileRetry(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", 0, "", false, nil
		}
		return "", 0, "", false, err
	}
	var e envelope
	if err := json.Unmarshal(b, &e); err != nil {
		return "", 0, "", false, err
	}
	return e.Value, e.Version, e.UpdatedAt, true, nil
}

func loadValue(key string) (string, int, string, bool, error) {
	if key == "conf_pedidos" {
		return loadOrders()
	}
	return loadGeneric(key)
}

func saveGeneric(key, value string, version int) error {
	e := envelope{Value: value, Version: version, UpdatedAt: nowISO()}
	b, _ := json.MarshalIndent(e, "", "  ")
	return atomicWrite(keyPath(key), b)
}

func saveValue(key, oldValue, newValue string, oldProvided, force bool) (envelope, error) {
	if len([]byte(newValue)) > maxBody {
		return envelope{}, errors.New("conteúdo excede 100 MB")
	}
	release, err := acquireFileLock(writeLockPath(key), 30*time.Second)
	if err != nil {
		return envelope{}, err
	}
	defer release()
	current, version, _, exists, err := loadValue(key)
	if err != nil {
		return envelope{}, err
	}
	old := ""
	if oldProvided {
		old = oldValue
	}
	merged := mergeValue(key, current, old, newValue, exists, force)
	if key == "conf_pedidos" || key == "conf_pedidos_rascunhos" {
		merged, err = filterCompletedOrders(merged)
		if err != nil {
			return envelope{}, err
		}
	}
	version++
	if key == "conf_pedidos" {
		err = saveOrders(merged)
	} else {
		err = saveGeneric(key, merged, version)
	}
	if err != nil {
		return envelope{}, err
	}
	_ = backupDaily()
	return envelope{Value: merged, Version: version, UpdatedAt: nowISO()}, nil
}

func deleteValue(key string) error {
	release, err := acquireFileLock(writeLockPath(key), 30*time.Second)
	if err != nil {
		return err
	}
	defer release()
	if key == "conf_pedidos" {
		dir := filepath.Join(dataDir, "PEDIDOS")
		entries, _ := os.ReadDir(dir)
		for _, e := range entries {
			if !e.IsDir() {
				_ = os.Remove(filepath.Join(dir, e.Name()))
			}
		}
		return nil
	}
	if err := os.Remove(keyPath(key)); err != nil && !os.IsNotExist(err) {
		return err
	}
	return nil
}

func listKeys() []string {
	set := map[string]bool{}
	if entries, _ := os.ReadDir(filepath.Join(dataDir, "PEDIDOS")); len(entries) > 0 {
		set["conf_pedidos"] = true
	}
	entries, _ := os.ReadDir(filepath.Join(dataDir, "BANCO"))
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
			continue
		}
		b, err := os.ReadFile(filepath.Join(dataDir, "BANCO", e.Name()))
		if err != nil {
			continue
		}
		var env envelope
		if json.Unmarshal(b, &env) != nil {
			continue
		}
		// O nome seguro não é reversível. Incluímos as chaves conhecidas abaixo.
	}
	known := []string{"conf_pedidos", "conf_pedidos_rascunhos", "conf_produtos", "conf_usuarios", "conf_solicitacoes_usuarios", "conf_etiquetas", "conf_fotos", "conf_historico_conferencias_v2", "conf_logs", "conf_banco_atualizado_em"}
	for _, k := range known {
		if _, _, _, exists, _ := loadValue(k); exists {
			set[k] = true
		}
	}
	keys := make([]string, 0, len(set))
	for k := range set {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func backupDaily() error {
	today := time.Now().Format("2006-01-02")
	target := filepath.Join(backupDir, "dados_"+today+".zip")
	if _, err := os.Stat(target); err == nil {
		return nil
	}
	release, err := acquireFileLock(filepath.Join(dataDir, "BLOQUEIOS", "backup_diario.lck"), 3*time.Second)
	if err != nil {
		return nil
	}
	defer release()
	if _, err := os.Stat(target); err == nil {
		return nil
	}
	if err := os.MkdirAll(backupDir, 0777); err != nil {
		return err
	}
	tmp := target + ".tmp"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	zw := zip.NewWriter(f)
	walkErr := filepath.Walk(dataDir, func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if info.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(dataDir, path)
		upper := strings.ToUpper(rel)
		if strings.Contains(upper, "BLOQUEIOS") || strings.Contains(upper, ".TMP.") || strings.HasSuffix(upper, ".LCK") {
			return nil
		}
		w, err := zw.Create(filepath.ToSlash(rel))
		if err != nil {
			return err
		}
		rf, err := os.Open(path)
		if err != nil {
			return nil
		}
		_, cpErr := io.Copy(w, rf)
		_ = rf.Close()
		return cpErr
	})
	closeErr := zw.Close()
	_ = f.Close()
	if walkErr != nil {
		_ = os.Remove(tmp)
		return walkErr
	}
	if closeErr != nil {
		_ = os.Remove(tmp)
		return closeErr
	}
	_ = os.Remove(target)
	if err := os.Rename(tmp, target); err != nil {
		return err
	}
	entries, _ := os.ReadDir(backupDir)
	type fi struct {
		path string
		mod  time.Time
	}
	var files []fi
	for _, e := range entries {
		if !e.IsDir() && strings.HasPrefix(e.Name(), "dados_") && strings.HasSuffix(e.Name(), ".zip") {
			if s, er := e.Info(); er == nil {
				files = append(files, fi{filepath.Join(backupDir, e.Name()), s.ModTime()})
			}
		}
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mod.After(files[j].mod) })
	if len(files) > 15 {
		for _, old := range files[15:] {
			_ = os.Remove(old.path)
		}
	}
	return nil
}

func orderLockFile(pedido string) string {
	return filepath.Join(dataDir, "BLOQUEIOS", "pedido_"+safeName(pedido)+".json")
}

func readOrderLock(pedido string) (*lockInfo, error) {
	b, err := os.ReadFile(orderLockFile(pedido))
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var li lockInfo
	if err := json.Unmarshal(b, &li); err != nil {
		return nil, err
	}
	t, err := time.Parse(time.RFC3339, li.Atualizado)
	if err != nil || time.Since(t) > staleOrderLock {
		_ = os.Remove(orderLockFile(pedido))
		return nil, nil
	}
	return &li, nil
}

func purgeCompletedOrdersFromGenericKey(key string) error {
	release, err := acquireFileLock(writeLockPath(key), 30*time.Second)
	if err != nil {
		return err
	}
	defer release()

	current, version, _, exists, err := loadGeneric(key)
	if err != nil || !exists {
		return err
	}
	filtered, err := filterCompletedOrders(current)
	if err != nil {
		return err
	}
	if filtered == current {
		return nil
	}
	return saveGeneric(key, filtered, version+1)
}

func handleOrderComplete(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		jsonResponse(w, map[string]any{"error": "Método inválido"}, 405)
		return
	}
	var req map[string]any
	if err := decodeBody(r, &req); err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 400)
		return
	}
	pedido := strings.TrimSpace(fmt.Sprint(req["pedido"]))
	if pedido == "" {
		jsonResponse(w, map[string]any{"error": "Pedido não informado."}, 400)
		return
	}

	release, err := acquireFileLock(writeLockPath("conf_pedidos"), 30*time.Second)
	if err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 409)
		return
	}
	defer release()

	if err := markOrderCompleted(pedido); err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 500)
		return
	}

	dir := filepath.Join(dataDir, "PEDIDOS")
	entries, _ := os.ReadDir(dir)
	encontrado := false
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(strings.ToLower(e.Name()), ".json") {
			continue
		}
		path := filepath.Join(dir, e.Name())
		b, readErr := os.ReadFile(path)
		if readErr != nil {
			continue
		}
		var f orderFile
		if json.Unmarshal(b, &f) != nil || normalize(f.Pedido) != normalize(pedido) {
			continue
		}
		archiveOrder(path, f, true)
		if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
			jsonResponse(w, map[string]any{"error": err.Error()}, 500)
			return
		}
		encontrado = true
		break
	}
	if err := purgeCompletedOrdersFromGenericKey("conf_pedidos_rascunhos"); err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 500)
		return
	}
	_ = os.Remove(orderLockFile(pedido))
	_ = backupDaily()
	jsonResponse(w, map[string]any{"ok": true, "pedido": pedido, "removed": encontrado, "already_removed": !encontrado}, 200)
}

func handleOrderLock(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		jsonResponse(w, map[string]any{"error": "Método inválido"}, 405)
		return
	}
	var req map[string]any
	if err := decodeBody(r, &req); err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 400)
		return
	}
	action := fmt.Sprint(req["action"])
	pedido := strings.TrimSpace(fmt.Sprint(req["pedido"]))
	clientID := strings.TrimSpace(fmt.Sprint(req["clientId"]))
	operador := strings.TrimSpace(fmt.Sprint(req["operador"]))
	if pedido == "" || clientID == "" {
		jsonResponse(w, map[string]any{"error": "Pedido ou identificação da máquina não informado."}, 400)
		return
	}
	release, err := acquireFileLock(writeLockPath("pedido_lock_"+pedido), 8*time.Second)
	if err != nil {
		jsonResponse(w, map[string]any{"error": err.Error()}, 409)
		return
	}
	defer release()
	current, _ := readOrderLock(pedido)
	host, _ := os.Hostname()
	switch action {
	case "acquire":
		if current != nil && current.ClientID != clientID {
			jsonResponse(w, map[string]any{"ok": false, "locked": true, "lock": current}, 409)
			return
		}
		started := nowISO()
		if current != nil && current.Iniciado != "" {
			started = current.Iniciado
		}
		li := lockInfo{Pedido: pedido, ClientID: clientID, Operador: operador, Computador: host, Atualizado: nowISO(), Iniciado: started}
		b, _ := json.MarshalIndent(li, "", "  ")
		if err := atomicWrite(orderLockFile(pedido), b); err != nil {
			jsonResponse(w, map[string]any{"error": err.Error()}, 500)
			return
		}
		jsonResponse(w, map[string]any{"ok": true, "lock": li}, 200)
	case "heartbeat":
		if current == nil {
			jsonResponse(w, map[string]any{"ok": false, "expired": true}, 409)
			return
		}
		if current.ClientID != clientID {
			jsonResponse(w, map[string]any{"ok": false, "locked": true, "lock": current}, 409)
			return
		}
		current.Atualizado = nowISO()
		b, _ := json.MarshalIndent(current, "", "  ")
		_ = atomicWrite(orderLockFile(pedido), b)
		jsonResponse(w, map[string]any{"ok": true}, 200)
	case "release":
		if current == nil || current.ClientID == clientID || req["force"] == true {
			_ = os.Remove(orderLockFile(pedido))
			jsonResponse(w, map[string]any{"ok": true}, 200)
			return
		}
		jsonResponse(w, map[string]any{"ok": false, "locked": true, "lock": current}, 409)
	case "status":
		jsonResponse(w, map[string]any{"ok": true, "lock": current}, 200)
	default:
		jsonResponse(w, map[string]any{"error": "Ação inválida."}, 400)
	}
}

func decodeBody(r *http.Request, out any) error {
	r.Body = http.MaxBytesReader(nil, r.Body, maxBody)
	dec := json.NewDecoder(r.Body)
	return dec.Decode(out)
}

func jsonResponse(w http.ResponseWriter, data any, status int) {
	b, _ := json.Marshal(data)
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_, _ = w.Write(b)
}

func storageHandler(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	switch r.Method {
	case http.MethodGet:
		if q.Get("list") == "1" {
			jsonResponse(w, map[string]any{"keys": listKeys()}, 200)
			return
		}
		if ks := q.Get("keys"); ks != "" {
			values := map[string]any{}
			versions := map[string]int{}
			for _, k := range strings.Split(ks, ",") {
				v, ver, _, exists, err := loadValue(k)
				if err != nil {
					values[k] = nil
					versions[k] = 0
				} else if exists {
					values[k] = v
					versions[k] = ver
				} else {
					values[k] = nil
					versions[k] = 0
				}
			}
			jsonResponse(w, map[string]any{"values": values, "versions": versions}, 200)
			return
		}
		key := q.Get("key")
		if key == "" {
			jsonResponse(w, map[string]any{"error": "Chave não informada."}, 400)
			return
		}
		v, ver, updated, exists, err := loadValue(key)
		if err != nil {
			jsonResponse(w, map[string]any{"error": err.Error()}, 500)
			return
		}
		jsonResponse(w, map[string]any{"exists": exists, "value": func() any {
			if exists {
				return v
			}
			return nil
		}(), "version": ver, "updated_at": updated}, 200)
	case http.MethodPost:
		var req map[string]any
		if err := decodeBody(r, &req); err != nil {
			jsonResponse(w, map[string]any{"error": err.Error()}, 400)
			return
		}
		key := strings.TrimSpace(fmt.Sprint(req["key"]))
		nv, ok := req["newValue"]
		if key == "" || !ok {
			jsonResponse(w, map[string]any{"error": "Chave ou valor inválido."}, 400)
			return
		}
		ov, oldProvided := req["oldValue"]
		e, err := saveValue(key, fmt.Sprint(ov), fmt.Sprint(nv), oldProvided, req["force"] == true)
		if err != nil {
			jsonResponse(w, map[string]any{"error": err.Error()}, 409)
			return
		}
		jsonResponse(w, map[string]any{"ok": true, "value": e.Value, "version": e.Version, "updated_at": e.UpdatedAt}, 200)
	case http.MethodDelete:
		key := q.Get("key")
		if key == "" {
			jsonResponse(w, map[string]any{"error": "Chave não informada."}, 400)
			return
		}
		if err := deleteValue(key); err != nil {
			jsonResponse(w, map[string]any{"error": err.Error()}, 500)
			return
		}
		jsonResponse(w, map[string]any{"ok": true}, 200)
	default:
		jsonResponse(w, map[string]any{"error": "Método inválido."}, 405)
	}
}

func staticHandler(w http.ResponseWriter, r *http.Request) {
	p := strings.TrimSpace(r.URL.Path)
	file := ""
	switch strings.ToLower(p) {
	case "", "/", "/conferencia", "/conferencia/", "/conferencia.html", "/pedidos", "/pedidos/":
		file = "conferencia.html"
	case "/admin", "/admin/", "/admin.html", "/administracao", "/administracao/":
		file = "admin.html"
	case "/shared-storage.js":
		file = "shared-storage.js"
	default:
		file = strings.TrimPrefix(p, "/")
	}
	clean := filepath.Clean(file)
	if strings.HasPrefix(clean, "..") {
		http.Error(w, "Acesso negado", http.StatusForbidden)
		return
	}
	full := filepath.Join(appDir, clean)
	st, err := os.Stat(full)
	if err != nil || st.IsDir() {
		// URLs digitadas sem extensão devem sempre voltar à tela de conferência,
		// evitando a página 404 quando um atalho antigo acrescentar um caminho.
		if filepath.Ext(clean) == "" {
			full = filepath.Join(appDir, "conferencia.html")
			st, err = os.Stat(full)
		}
		if err != nil || st.IsDir() {
			http.NotFound(w, r)
			return
		}
	}
	if ext := filepath.Ext(full); ext != "" {
		if mt := mime.TypeByExtension(ext); mt != "" {
			w.Header().Set("Content-Type", mt)
		}
	}
	if strings.HasSuffix(strings.ToLower(full), ".html") || strings.HasSuffix(strings.ToLower(full), ".js") {
		w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")
	}
	conteudo, err := os.ReadFile(full)
	if err != nil {
		http.Error(w, "Não foi possível abrir o arquivo do aplicativo: "+err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("X-Noromak-Version", appVersion)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(conteudo)
}

func healthInfo(port int) (map[string]any, bool) {
	c := http.Client{Timeout: 650 * time.Millisecond}
	resp, err := c.Get(fmt.Sprintf("http://127.0.0.1:%d/api/health", port))
	if err != nil {
		return nil, false
	}
	defer resp.Body.Close()
	var d map[string]any
	if json.NewDecoder(resp.Body).Decode(&d) != nil || d["marker"] != appMarker {
		return nil, false
	}
	return d, true
}

func samePath(a, b string) bool {
	aa, _ := filepath.Abs(a)
	bb, _ := filepath.Abs(b)
	return strings.EqualFold(filepath.Clean(aa), filepath.Clean(bb))
}

func matchingInstance(info map[string]any) bool {
	return fmt.Sprint(info["version"]) == appVersion && samePath(fmt.Sprint(info["root_dir"]), rootDir)
}

func findRunningPort() int {
	for p := firstPort; p <= lastPort; p++ {
		if info, ok := healthInfo(p); ok && matchingInstance(info) {
			return p
		}
	}
	return 0
}

func shutdownIncompatibleInstances() {
	for p := firstPort; p <= lastPort; p++ {
		info, ok := healthInfo(p)
		if !ok || matchingInstance(info) {
			continue
		}
		req, _ := http.NewRequest(http.MethodPost, fmt.Sprintf("http://127.0.0.1:%d/api/shutdown", p), nil)
		client := http.Client{Timeout: 700 * time.Millisecond}
		resp, err := client.Do(req)
		if err == nil && resp != nil {
			_ = resp.Body.Close()
		}
	}
	time.Sleep(250 * time.Millisecond)
}

func openBrowser(target string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("cmd", "/c", "start", "", target)
	case "darwin":
		cmd = exec.Command("open", target)
	default:
		cmd = exec.Command("xdg-open", target)
	}
	_ = cmd.Start()
}

func chooseListener() (net.Listener, int, error) {
	for p := firstPort; p <= lastPort; p++ {
		ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", p))
		if err == nil {
			return ln, p, nil
		}
	}
	return nil, 0, errors.New("nenhuma porta local disponível entre 8765 e 8774")
}

func verifySharedWrite() error {
	dirs := []string{appDir, dataDir, filepath.Join(dataDir, "PEDIDOS"), filepath.Join(dataDir, "RASCUNHOS"), filepath.Join(dataDir, "BANCO"), filepath.Join(dataDir, "BLOQUEIOS"), filepath.Join(dataDir, "CONCLUIDOS"), filepath.Join(dataDir, "CONCLUIDOS", "INDICE"), filepath.Join(dataDir, "REMOVIDOS"), backupDir, logDir}
	for _, d := range dirs {
		if err := os.MkdirAll(d, 0777); err != nil {
			return err
		}
	}
	test := filepath.Join(dataDir, fmt.Sprintf(".teste_escrita_%d.tmp", os.Getpid()))
	if err := os.WriteFile(test, []byte("ok"), 0666); err != nil {
		return fmt.Errorf("sem permissão para gravar em %s: %w", dataDir, err)
	}
	return os.Remove(test)
}

func setupLog() {
	_ = os.MkdirAll(logDir, 0777)
	host, _ := os.Hostname()
	f, err := os.OpenFile(filepath.Join(logDir, "aplicativo_"+safeName(host)+".log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err == nil {
		log.SetOutput(f)
	}
	log.SetFlags(log.Ldate | log.Ltime | log.Lshortfile)
}

func fatalStartup(err error) {
	path := filepath.Join(rootDir, "ERRO_AO_ABRIR.txt")
	_ = os.WriteFile(path, []byte("Não foi possível abrir o Sistema de Conferência.\r\n\r\n"+err.Error()+"\r\n\r\nVerifique se a unidade G: está conectada e se você possui permissão para criar e alterar arquivos na pasta.\r\n"), 0666)
	if runtime.GOOS == "windows" {
		_ = exec.Command("notepad.exe", path).Start()
	}
}

func initPaths() error {
	exe, err := os.Executable()
	if err != nil {
		return err
	}
	rootDir = filepath.Dir(exe)
	appDir = filepath.Join(rootDir, "APP")
	dataDir = filepath.Join(rootDir, "DADOS")
	backupDir = filepath.Join(rootDir, "BACKUPS")
	logDir = filepath.Join(rootDir, "LOGS")
	return nil
}

func main() {
	if err := initPaths(); err != nil {
		return
	}
	setupLog()
	page := "admin"
	shutdown := false
	configurarQZ := false
	for _, a := range os.Args[1:] {
		switch a {
		case "--conferencia":
			page = "conferencia"
		case "--admin":
			page = "admin"
		case "--encerrar":
			shutdown = true
		case "--configurar-qz":
			configurarQZ = true
		}
	}
	if configurarQZ {
		if err := configureQZTray(); err != nil {
			fmt.Println("ERRO:", err)
			os.Exit(1)
		}
		fmt.Println("QZ Tray configurado com sucesso.")
		return
	}
	shutdownIncompatibleInstances()
	if shutdown {
		if p := findRunningPort(); p > 0 {
			req, _ := http.NewRequest(http.MethodPost, fmt.Sprintf("http://127.0.0.1:%d/api/shutdown", p), nil)
			_, _ = http.DefaultClient.Do(req)
		}
		return
	}
	if p := findRunningPort(); p > 0 {
		openBrowser(fmt.Sprintf("http://127.0.0.1:%d/%s.html?v=%s", p, page, url.QueryEscape(appVersion)))
		return
	}
	if err := verifySharedWrite(); err != nil {
		log.Println(err)
		fatalStartup(err)
		return
	}
	ln, port, err := chooseListener()
	if err != nil {
		fatalStartup(err)
		return
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/health", func(w http.ResponseWriter, r *http.Request) {
		host, _ := os.Hostname()
		jsonResponse(w, map[string]any{"ok": true, "marker": appMarker, "version": appVersion, "root_dir": rootDir, "computer": host, "shared_data_path": dataDir, "server_time": nowISO()}, 200)
	})
	mux.HandleFunc("/api/storage", storageHandler)
	mux.HandleFunc("/api/qz/certificate", handleQZCertificate)
	mux.HandleFunc("/api/qz/sign", handleQZSign)
	mux.HandleFunc("/api/order-lock", handleOrderLock)
	mux.HandleFunc("/api/order-complete", handleOrderComplete)
	mux.HandleFunc("/api/shutdown", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			jsonResponse(w, map[string]any{"error": "Método inválido"}, 405)
			return
		}
		jsonResponse(w, map[string]any{"ok": true}, 200)
		go func() {
			time.Sleep(150 * time.Millisecond)
			serverMu.Lock()
			if srv != nil {
				_ = srv.Close()
			}
			serverMu.Unlock()
		}()
	})
	mux.HandleFunc("/", staticHandler)
	srv = &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	_ = backupDaily()
	go func() {
		time.Sleep(700 * time.Millisecond)
		openBrowser(fmt.Sprintf("http://127.0.0.1:%d/%s.html?v=%s", port, page, url.QueryEscape(appVersion)))
	}()
	log.Printf("Aplicativo iniciado em 127.0.0.1:%d; dados=%s", port, dataDir)
	err = srv.Serve(ln)
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Println(err)
		fatalStartup(err)
	}
}

// Mantém importações usadas em builds que tratam URLs de forma distinta.
var _ = url.QueryEscape
