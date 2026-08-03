package main

import (
	"bytes"
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha512"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

var (
	qzKeyOnce sync.Once
	qzKey     *rsa.PrivateKey
	qzKeyErr  error
)

func qzSigningKey() (*rsa.PrivateKey, error) {
	qzKeyOnce.Do(func() {
		block, _ := pem.Decode([]byte(qzPrivateKeyPEM))
		if block == nil {
			qzKeyErr = errors.New("chave privada QZ inválida")
			return
		}
		if parsed, err := x509.ParsePKCS8PrivateKey(block.Bytes); err == nil {
			var ok bool
			qzKey, ok = parsed.(*rsa.PrivateKey)
			if !ok {
				qzKeyErr = errors.New("a chave QZ não é RSA")
			}
			return
		}
		if parsed, err := x509.ParsePKCS1PrivateKey(block.Bytes); err == nil {
			qzKey = parsed
			return
		}
		qzKeyErr = errors.New("não foi possível interpretar a chave privada QZ")
	})
	return qzKey, qzKeyErr
}

func handleQZCertificate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "Método inválido", http.StatusMethodNotAllowed)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store, no-cache, must-revalidate")
	_, _ = io.WriteString(w, qzCertificatePEM)
}

func handleQZSign(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "Método inválido", http.StatusMethodNotAllowed)
		return
	}
	defer r.Body.Close()
	payload, err := io.ReadAll(io.LimitReader(r.Body, 4*1024*1024))
	if err != nil || len(payload) == 0 {
		http.Error(w, "Solicitação de assinatura inválida", http.StatusBadRequest)
		return
	}
	key, err := qzSigningKey()
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	digest := sha512.Sum512(payload)
	sig, err := rsa.SignPKCS1v15(rand.Reader, key, crypto.SHA512, digest[:])
	if err != nil {
		http.Error(w, "Falha ao assinar a solicitação do QZ Tray", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = io.WriteString(w, base64.StdEncoding.EncodeToString(sig))
}

func qzInstallCandidates(filename string) []string {
	var result []string
	for _, base := range []string{os.Getenv("PROGRAMFILES"), os.Getenv("PROGRAMFILES(X86)"), os.Getenv("ProgramW6432")} {
		if strings.TrimSpace(base) == "" {
			continue
		}
		result = append(result, filepath.Join(base, "QZ Tray", filename))
	}
	if local := strings.TrimSpace(os.Getenv("LOCALAPPDATA")); local != "" {
		result = append(result,
			filepath.Join(local, "Programs", "QZ Tray", filename),
			filepath.Join(local, "QZ Tray", filename),
		)
	}
	return result
}

func firstExisting(paths []string) string {
	seen := map[string]bool{}
	for _, p := range paths {
		key := strings.ToLower(filepath.Clean(p))
		if seen[key] {
			continue
		}
		seen[key] = true
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	return ""
}

func writeQZSetupResult(lines []string, setupErr error) {
	if setupErr != nil {
		lines = append(lines, "", "ERRO: "+setupErr.Error())
	} else {
		lines = append(lines, "", "CONFIGURACAO CONCLUIDA COM SUCESSO.")
	}
	path := filepath.Join(rootDir, "CONFIGURACAO_QZ_RESULTADO.txt")
	_ = os.WriteFile(path, []byte(strings.Join(lines, "\r\n")+"\r\n"), 0666)
}

func verifyQZCertificateChain() error {
	rootBlock, _ := pem.Decode([]byte(qzRootCACertPEM))
	leafBlock, _ := pem.Decode([]byte(qzCertificatePEM))
	if rootBlock == nil || leafBlock == nil {
		return errors.New("certificado QZ interno inválido")
	}
	root, err := x509.ParseCertificate(rootBlock.Bytes)
	if err != nil {
		return fmt.Errorf("raiz QZ inválida: %w", err)
	}
	leaf, err := x509.ParseCertificate(leafBlock.Bytes)
	if err != nil {
		return fmt.Errorf("certificado QZ inválido: %w", err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(root)
	_, err = leaf.Verify(x509.VerifyOptions{Roots: pool, CurrentTime: time.Now(), KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageAny}})
	if err != nil {
		return fmt.Errorf("cadeia do certificado QZ inválida: %w", err)
	}
	return nil
}

func upsertProperty(path, key, value string) error {
	var content []byte
	if b, err := os.ReadFile(path); err == nil {
		content = b
	} else if !os.IsNotExist(err) {
		return err
	}

	lineEnding := "\r\n"
	if bytes.Contains(content, []byte("\n")) && !bytes.Contains(content, []byte("\r\n")) {
		lineEnding = "\n"
	}
	text := strings.ReplaceAll(string(content), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	prefix := strings.ToLower(key) + "="
	replaced := false
	out := make([]string, 0, len(lines)+1)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "#") && !strings.HasPrefix(trimmed, "!") && strings.HasPrefix(strings.ToLower(trimmed), prefix) {
			if !replaced {
				out = append(out, key+"="+value)
				replaced = true
			}
			continue
		}
		out = append(out, line)
	}
	if !replaced {
		if len(out) > 0 && strings.TrimSpace(out[len(out)-1]) != "" {
			out = append(out, "")
		}
		out = append(out, key+"="+value)
	}
	for len(out) > 0 && out[len(out)-1] == "" {
		out = out[:len(out)-1]
	}
	return os.WriteFile(path, []byte(strings.Join(out, lineEnding)+lineEnding), 0644)
}

func writeAndVerify(path string, data []byte) error {
	if err := os.WriteFile(path, data, 0644); err != nil {
		return err
	}
	got, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if !bytes.Equal(bytes.TrimSpace(got), bytes.TrimSpace(data)) {
		return errors.New("o arquivo gravado não corresponde ao certificado esperado")
	}
	return nil
}

// configureQZTray instala a raiz personalizada no próprio QZ Tray. A versão
// anterior configurava apenas QZ_OPTS; em algumas instalações do Windows o
// inicializador do QZ não herdava essa variável e o certificado continuava
// aparecendo como Invalid Certificate. A 6.9 usa os três mecanismos oficiais:
// override.crt, authcert.override e QZ_OPTS.
func configureQZTray() error {
	lines := []string{
		"CONFIGURACAO QZ TRAY - SISTEMA DE CONFERENCIA 6.9",
		"Data: " + time.Now().Format("02/01/2006 15:04:05"),
	}
	fail := func(err error) error {
		writeQZSetupResult(lines, err)
		return err
	}

	if runtime.GOOS != "windows" {
		return fail(errors.New("esta configuração automática está disponível somente no Windows"))
	}
	if err := verifyQZCertificateChain(); err != nil {
		return fail(err)
	}
	lines = append(lines, "Cadeia interna do certificado validada.")

	consolePath := firstExisting(qzInstallCandidates("qz-tray-console.exe"))
	trayPath := firstExisting(qzInstallCandidates("qz-tray.exe"))
	if consolePath == "" {
		return fail(errors.New("QZ Tray não encontrado. Instale o QZ Tray 2.2 ou superior"))
	}
	qzDir := filepath.Dir(consolePath)
	lines = append(lines, "QZ Tray encontrado em: "+qzDir)

	baseCertDir := strings.TrimSpace(os.Getenv("PUBLIC"))
	if baseCertDir == "" {
		baseCertDir = strings.TrimSpace(os.Getenv("PROGRAMDATA"))
	}
	if baseCertDir == "" {
		baseCertDir = strings.TrimSpace(os.Getenv("LOCALAPPDATA"))
	}
	if baseCertDir == "" {
		return fail(errors.New("não foi encontrada uma pasta local do Windows para instalar os certificados"))
	}
	certDir := filepath.Join(baseCertDir, "NoromakQZ")
	if err := os.MkdirAll(certDir, 0755); err != nil {
		return fail(err)
	}
	rootPath := filepath.Join(certDir, "qz-root-ca.crt")
	certPath := filepath.Join(certDir, "digital-certificate.txt")
	if err := writeAndVerify(rootPath, []byte(qzRootCACertPEM)); err != nil {
		return fail(fmt.Errorf("não foi possível gravar a raiz pública: %w", err))
	}
	if err := writeAndVerify(certPath, []byte(qzCertificatePEM)); err != nil {
		return fail(fmt.Errorf("não foi possível gravar o certificado público: %w", err))
	}
	lines = append(lines, "Certificados públicos copiados para: "+certDir)

	// Mecanismo principal e persistente: override.crt dentro da instalação.
	overridePath := filepath.Join(qzDir, "override.crt")
	if err := writeAndVerify(overridePath, []byte(qzRootCACertPEM)); err != nil {
		return fail(fmt.Errorf("não foi possível instalar override.crt no QZ Tray. Execute o configurador aceitando a solicitação do Windows: %w", err))
	}
	lines = append(lines, "Raiz instalada em: "+overridePath)

	// Mecanismo adicional: propriedade explícita no arquivo principal do QZ.
	propertiesPath := filepath.Join(qzDir, "qz-tray.properties")
	propertyValue := filepath.ToSlash(overridePath)
	if err := upsertProperty(propertiesPath, "authcert.override", propertyValue); err != nil {
		return fail(fmt.Errorf("não foi possível atualizar qz-tray.properties: %w", err))
	}
	lines = append(lines, "Propriedade authcert.override configurada em qz-tray.properties.")

	// Fallback oficial para inicializações que honram variável de ambiente.
	qzOpts := "-DtrustedRootCert=" + rootPath
	setx := exec.Command("setx", "QZ_OPTS", qzOpts)
	if out, err := setx.CombinedOutput(); err != nil {
		return fail(fmt.Errorf("não foi possível definir QZ_OPTS: %v (%s)", err, strings.TrimSpace(string(out))))
	}
	lines = append(lines, "QZ_OPTS configurado sem aspas internas.")

	env := append(os.Environ(), "QZ_OPTS="+qzOpts)
	_ = exec.Command("taskkill", "/IM", "qz-tray.exe", "/F").Run()
	time.Sleep(1200 * time.Millisecond)

	allow := exec.Command(consolePath, "--allow", certPath)
	allow.Env = env
	allow.Dir = qzDir
	if out, err := allow.CombinedOutput(); err != nil {
		return fail(fmt.Errorf("não foi possível autorizar o certificado no QZ Tray: %v (%s)", err, strings.TrimSpace(string(out))))
	}
	lines = append(lines, "Certificado incluído na lista de sites permitidos do QZ Tray.")

	if trayPath != "" {
		start := exec.Command(trayPath, "--steal")
		start.Env = env
		start.Dir = qzDir
		if err := start.Start(); err != nil {
			return fail(fmt.Errorf("a configuração foi gravada, mas o QZ Tray não reabriu: %w", err))
		}
		lines = append(lines, "QZ Tray reiniciado usando a raiz personalizada.", "Observacao: na chamada connect, Signature = Not Required e normal; a validade deve aparecer como Valid.")
	} else {
		lines = append(lines, "Aviso: abra o QZ Tray manualmente pelo menu Iniciar.")
	}

	writeQZSetupResult(lines, nil)
	return nil
}
