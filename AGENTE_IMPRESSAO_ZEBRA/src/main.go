//go:build windows

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

const agentVersion = "1.0.0"

type Config struct {
	ServerURL   string `json:"server_url"`
	Token       string `json:"token"`
	PrinterName string `json:"printer_name"`
	Mode        string `json:"mode"`
	TCPAddress  string `json:"tcp_address"`
	PollSeconds int    `json:"poll_seconds"`
}

type PrintJob struct {
	ID          string   `json:"id"`
	OrderID     string   `json:"order_id"`
	Kind        string   `json:"kind"`
	PrinterName string   `json:"printer_name"`
	Documents   []string `json:"documents"`
}

type NextResponse struct {
	OK  bool      `json:"ok"`
	Job *PrintJob `json:"job"`
}

type APIError struct {
	Error string `json:"error"`
}

type docInfo1 struct {
	DocName    *uint16
	OutputFile *uint16
	DataType   *uint16
}

var (
	winspool         = syscall.NewLazyDLL("winspool.drv")
	procOpenPrinterW = winspool.NewProc("OpenPrinterW")
	procClosePrinter = winspool.NewProc("ClosePrinter")
	procStartDoc     = winspool.NewProc("StartDocPrinterW")
	procEndDoc       = winspool.NewProc("EndDocPrinter")
	procStartPage    = winspool.NewProc("StartPagePrinter")
	procEndPage      = winspool.NewProc("EndPagePrinter")
	procWritePrinter = winspool.NewProc("WritePrinter")
	kernel32         = syscall.NewLazyDLL("kernel32.dll")
	procCreateMutexW = kernel32.NewProc("CreateMutexW")
	procGetLastError = kernel32.NewProc("GetLastError")
)

func programDataDir() string {
	base := os.Getenv("LOCALAPPDATA")
	if strings.TrimSpace(base) == "" {
		base = filepath.Join(os.Getenv("USERPROFILE"), "AppData", "Local")
	}
	return filepath.Join(base, "NoromakPrintAgent")
}

func configPath() string { return filepath.Join(programDataDir(), "config.json") }
func logPath() string    { return filepath.Join(programDataDir(), "agent.log") }

func setupLogger() (*os.File, error) {
	if err := os.MkdirAll(programDataDir(), 0o755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(logPath(), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	log.SetOutput(f)
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	return f, nil
}

func singleInstance() error {
	name, _ := syscall.UTF16PtrFromString("Local\\NoromakPrintAgent")
	h, _, _ := procCreateMutexW.Call(0, 0, uintptr(unsafe.Pointer(name)))
	if h == 0 {
		return errors.New("não foi possível criar o controle de instância única")
	}
	last, _, _ := procGetLastError.Call()
	if last == 183 { // ERROR_ALREADY_EXISTS
		return errors.New("o Noromak Print Agent já está em execução")
	}
	return nil
}

func loadConfig() (Config, error) {
	raw, err := os.ReadFile(configPath())
	if err != nil {
		return Config{}, fmt.Errorf("configuração não encontrada em %s: %w", configPath(), err)
	}
	raw = bytes.TrimPrefix(raw, []byte{0xEF, 0xBB, 0xBF})
	var cfg Config
	if err := json.Unmarshal(raw, &cfg); err != nil {
		return Config{}, fmt.Errorf("configuração inválida: %w", err)
	}
	cfg.ServerURL = strings.TrimRight(strings.TrimSpace(cfg.ServerURL), "/")
	cfg.Token = strings.TrimSpace(cfg.Token)
	cfg.PrinterName = strings.TrimSpace(cfg.PrinterName)
	cfg.Mode = strings.ToLower(strings.TrimSpace(cfg.Mode))
	cfg.TCPAddress = strings.TrimSpace(cfg.TCPAddress)
	if cfg.Mode == "" {
		cfg.Mode = "windows"
	}
	if cfg.PollSeconds < 1 || cfg.PollSeconds > 60 {
		cfg.PollSeconds = 2
	}
	if cfg.ServerURL == "" || cfg.Token == "" {
		return Config{}, errors.New("server_url e token são obrigatórios")
	}
	if cfg.Mode == "windows" && cfg.PrinterName == "" {
		return Config{}, errors.New("printer_name é obrigatório no modo windows")
	}
	if cfg.Mode == "tcp" && cfg.TCPAddress == "" {
		return Config{}, errors.New("tcp_address é obrigatório no modo tcp")
	}
	return cfg, nil
}

func machineID() string {
	host, _ := os.Hostname()
	raw := host + "|" + os.Getenv("USERNAME") + "|NOROMAK"
	sum := sha256.Sum256([]byte(raw))
	return strings.ToUpper(host) + "-" + hex.EncodeToString(sum[:6])
}

func apiRequest(client *http.Client, cfg Config, method, path string, payload any, target any) error {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, cfg.ServerURL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+cfg.Token)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "NoromakPrintAgent/"+agentVersion)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json; charset=utf-8")
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(resp.Body, 8*1024*1024))
	if err != nil {
		return err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var apiErr APIError
		_ = json.Unmarshal(raw, &apiErr)
		if apiErr.Error == "" {
			apiErr.Error = strings.TrimSpace(string(raw))
		}
		return fmt.Errorf("servidor respondeu HTTP %d: %s", resp.StatusCode, apiErr.Error)
	}
	if target != nil && len(raw) > 0 {
		if err := json.Unmarshal(raw, target); err != nil {
			return fmt.Errorf("resposta inválida do servidor: %w", err)
		}
	}
	return nil
}

func fetchNext(client *http.Client, cfg Config) (*PrintJob, error) {
	query := fmt.Sprintf("?agent_id=%s&computer=%s&printer=%s&mode=%s&version=%s",
		urlQuery(machineID()), urlQuery(hostname()), urlQuery(cfg.PrinterName), urlQuery(cfg.Mode), urlQuery(agentVersion))
	var out NextResponse
	if err := apiRequest(client, cfg, http.MethodGet, "/api/print/agent/next"+query, nil, &out); err != nil {
		return nil, err
	}
	return out.Job, nil
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil || strings.TrimSpace(h) == "" {
		return "COMPUTADOR-DESCONHECIDO"
	}
	return h
}

func urlQuery(s string) string {
	// QueryEscape mínimo sem dependência extra e suficiente para nomes de impressora.
	repl := strings.NewReplacer(
		"%", "%25", " ", "%20", "#", "%23", "&", "%26", "+", "%2B",
		"?", "%3F", "=", "%3D", "/", "%2F", "\\", "%5C", ":", "%3A",
	)
	return repl.Replace(s)
}

func reportResult(client *http.Client, cfg Config, job PrintJob, success bool, errText string) error {
	payload := map[string]any{
		"job_id":       job.ID,
		"agent_id":     machineID(),
		"computer":     hostname(),
		"printer_name": effectivePrinter(cfg, job),
		"success":      success,
		"error":        errText,
		"version":      agentVersion,
	}
	return apiRequest(client, cfg, http.MethodPost, "/api/print/agent/result", payload, nil)
}

func effectivePrinter(cfg Config, job PrintJob) string {
	if cfg.Mode == "tcp" {
		return cfg.TCPAddress
	}
	if cfg.PrinterName != "" {
		return cfg.PrinterName
	}
	return job.PrinterName
}

func printJob(cfg Config, job PrintJob) error {
	if len(job.Documents) == 0 {
		return errors.New("trabalho sem documentos ZPL")
	}
	payload := strings.Join(job.Documents, "\r\n")
	if !strings.Contains(payload, "^XA") || !strings.Contains(payload, "^XZ") {
		return errors.New("conteúdo recebido não parece ser ZPL válido")
	}
	if cfg.Mode == "tcp" {
		return printTCP(cfg.TCPAddress, []byte(payload))
	}
	return printWindowsRaw(effectivePrinter(cfg, job), []byte(payload), "Noromak - Pedido "+job.OrderID)
}

func printTCP(address string, data []byte) error {
	if !strings.Contains(address, ":") {
		address += ":9100"
	}
	conn, err := net.DialTimeout("tcp", address, 8*time.Second)
	if err != nil {
		return fmt.Errorf("não foi possível conectar em %s: %w", address, err)
	}
	defer conn.Close()
	_ = conn.SetWriteDeadline(time.Now().Add(15 * time.Second))
	n, err := conn.Write(data)
	if err != nil {
		return fmt.Errorf("falha ao enviar ZPL: %w", err)
	}
	if n != len(data) {
		return fmt.Errorf("envio incompleto: %d de %d bytes", n, len(data))
	}
	return nil
}

func printWindowsRaw(printerName string, data []byte, docName string) error {
	printerPtr, err := syscall.UTF16PtrFromString(printerName)
	if err != nil {
		return err
	}
	var printer uintptr
	ret, _, callErr := procOpenPrinterW.Call(uintptr(unsafe.Pointer(printerPtr)), uintptr(unsafe.Pointer(&printer)), 0)
	if ret == 0 {
		return fmt.Errorf("não foi possível abrir a impressora %q: %v", printerName, callErr)
	}
	defer procClosePrinter.Call(printer)

	docPtr, _ := syscall.UTF16PtrFromString(docName)
	rawPtr, _ := syscall.UTF16PtrFromString("RAW")
	info := docInfo1{DocName: docPtr, DataType: rawPtr}
	docID, _, callErr := procStartDoc.Call(printer, 1, uintptr(unsafe.Pointer(&info)))
	if docID == 0 {
		return fmt.Errorf("StartDocPrinter falhou: %v", callErr)
	}
	docStarted := true
	defer func() {
		if docStarted {
			procEndDoc.Call(printer)
		}
	}()

	ret, _, callErr = procStartPage.Call(printer)
	if ret == 0 {
		return fmt.Errorf("StartPagePrinter falhou: %v", callErr)
	}
	pageStarted := true
	defer func() {
		if pageStarted {
			procEndPage.Call(printer)
		}
	}()

	if len(data) == 0 {
		return errors.New("nenhum byte para imprimir")
	}
	var written uint32
	ret, _, callErr = procWritePrinter.Call(
		printer,
		uintptr(unsafe.Pointer(&data[0])),
		uintptr(uint32(len(data))),
		uintptr(unsafe.Pointer(&written)),
	)
	if ret == 0 {
		return fmt.Errorf("WritePrinter falhou: %v", callErr)
	}
	if int(written) != len(data) {
		return fmt.Errorf("a fila aceitou apenas %d de %d bytes", written, len(data))
	}

	ret, _, callErr = procEndPage.Call(printer)
	pageStarted = false
	if ret == 0 {
		return fmt.Errorf("EndPagePrinter falhou: %v", callErr)
	}
	ret, _, callErr = procEndDoc.Call(printer)
	docStarted = false
	if ret == 0 {
		return fmt.Errorf("EndDocPrinter falhou: %v", callErr)
	}
	return nil
}

func main() {
	f, err := setupLogger()
	if err == nil {
		defer f.Close()
	}
	defer func() {
		if r := recover(); r != nil {
			log.Printf("PANIC: %v\n%s", r, debug.Stack())
		}
	}()
	if err := singleInstance(); err != nil {
		log.Printf("Encerrando: %v", err)
		return
	}
	cfg, err := loadConfig()
	if err != nil {
		log.Printf("Configuração inválida: %v", err)
		return
	}
	client := &http.Client{Timeout: 25 * time.Second}
	log.Printf("Noromak Print Agent %s iniciado. Servidor=%s Modo=%s Impressora=%s", agentVersion, cfg.ServerURL, cfg.Mode, effectivePrinter(cfg, PrintJob{}))

	delay := time.Duration(cfg.PollSeconds) * time.Second
	consecutiveErrors := 0
	for {
		job, err := fetchNext(client, cfg)
		if err != nil {
			consecutiveErrors++
			if consecutiveErrors == 1 || consecutiveErrors%15 == 0 {
				log.Printf("Falha ao consultar fila: %v", err)
			}
			time.Sleep(minDuration(delay*time.Duration(minInt(consecutiveErrors, 15)), 30*time.Second))
			continue
		}
		consecutiveErrors = 0
		if job == nil {
			time.Sleep(delay)
			continue
		}

		log.Printf("Trabalho recebido: id=%s pedido=%s documentos=%d", job.ID, job.OrderID, len(job.Documents))
		printErr := printJob(cfg, *job)
		if printErr != nil {
			log.Printf("Falha na impressão %s: %v", job.ID, printErr)
			if err := reportResult(client, cfg, *job, false, printErr.Error()); err != nil {
				log.Printf("Falha ao informar erro ao servidor: %v", err)
			}
			time.Sleep(delay)
			continue
		}
		log.Printf("Impressão enviada à fila do Windows: id=%s pedido=%s", job.ID, job.OrderID)
		if err := reportResult(client, cfg, *job, true, ""); err != nil {
			log.Printf("ATENÇÃO: impressão realizada, mas não foi possível confirmar ao servidor: %v", err)
		}
	}
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func minDuration(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}
