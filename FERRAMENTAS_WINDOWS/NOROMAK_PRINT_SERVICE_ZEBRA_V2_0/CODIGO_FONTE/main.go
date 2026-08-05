//go:build windows

package main

import (
    "bytes"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "errors"
    "fmt"
    "html"
    "log"
    "net"
    "net/http"
    "os"
    "path/filepath"
    "sort"
    "strings"
    "sync"
    "syscall"
    "time"
    "unsafe"
)

const (
    appName    = "Noromak Print Service"
    appVersion = "2.0.0"
    defaultPort = 18181
    maxBodySize = 24 << 20
)

type Config struct {
    Printer        string   `json:"printer"`
    Port           int      `json:"port"`
    AllowedOrigins []string `json:"allowed_origins"`
}

type State struct {
    Printed map[string]string `json:"printed"`
}

type App struct {
    mu        sync.Mutex
    cfg       Config
    cfgPath   string
    statePath string
    logPath   string
    started   time.Time
    state     State
    logger    *log.Logger
}

type PrintRequest struct {
    JobID       string   `json:"job_id"`
    OrderID     string   `json:"order_id"`
    Marketplace string   `json:"marketplace"`
    Title       string   `json:"title"`
    Printer     string   `json:"printer"`
    Payload     []string `json:"payload"`
}

type JSONResponse map[string]any

var (
    modWinspool      = syscall.NewLazyDLL("winspool.drv")
    procOpenPrinterW = modWinspool.NewProc("OpenPrinterW")
    procClosePrinter = modWinspool.NewProc("ClosePrinter")
    procStartDocW    = modWinspool.NewProc("StartDocPrinterW")
    procEndDoc       = modWinspool.NewProc("EndDocPrinter")
    procStartPage    = modWinspool.NewProc("StartPagePrinter")
    procEndPage      = modWinspool.NewProc("EndPagePrinter")
    procWritePrinter = modWinspool.NewProc("WritePrinter")
    procEnumPrintersW = modWinspool.NewProc("EnumPrintersW")
)

type docInfo1 struct {
    DocName    *uint16
    OutputFile *uint16
    DataType   *uint16
}

type printerInfo4 struct {
    PrinterName *uint16
    ServerName  *uint16
    Attributes  uint32
}

func localDataDir() string {
    base := os.Getenv("LOCALAPPDATA")
    if base == "" {
        base = os.TempDir()
    }
    return filepath.Join(base, "NoromakPrintService")
}

func defaultConfig() Config {
    return Config{
        Printer: "ZDesigner ZD220-203dpi ZPL",
        Port: defaultPort,
        AllowedOrigins: []string{
            "https://noromakweb.com.br",
            "https://www.noromakweb.com.br",
        },
    }
}

func loadConfig(path string) (Config, error) {
    cfg := defaultConfig()
    data, err := os.ReadFile(path)
    if errors.Is(err, os.ErrNotExist) {
        return cfg, nil
    }
    if err != nil {
        return cfg, err
    }
    if err := json.Unmarshal(data, &cfg); err != nil {
        return cfg, err
    }
    if cfg.Port <= 0 || cfg.Port > 65535 {
        cfg.Port = defaultPort
    }
    if len(cfg.AllowedOrigins) == 0 {
        cfg.AllowedOrigins = defaultConfig().AllowedOrigins
    }
    return cfg, nil
}

func loadState(path string) State {
    state := State{Printed: map[string]string{}}
    data, err := os.ReadFile(path)
    if err == nil {
        _ = json.Unmarshal(data, &state)
    }
    if state.Printed == nil {
        state.Printed = map[string]string{}
    }
    return state
}

func (a *App) saveStateLocked() error {
    if len(a.state.Printed) > 3000 {
        type item struct{ ID, At string }
        items := make([]item, 0, len(a.state.Printed))
        for id, at := range a.state.Printed {
            items = append(items, item{id, at})
        }
        sort.Slice(items, func(i, j int) bool { return items[i].At > items[j].At })
        trimmed := make(map[string]string, 2500)
        for i := 0; i < len(items) && i < 2500; i++ {
            trimmed[items[i].ID] = items[i].At
        }
        a.state.Printed = trimmed
    }
    data, err := json.MarshalIndent(a.state, "", "  ")
    if err != nil {
        return err
    }
    tmp := a.statePath + ".tmp"
    if err := os.WriteFile(tmp, data, 0600); err != nil {
        return err
    }
    return os.Rename(tmp, a.statePath)
}

func utf16Ptr(s string) (*uint16, error) {
    return syscall.UTF16PtrFromString(s)
}

func rawPrint(printer, title string, data []byte) error {
    if strings.TrimSpace(printer) == "" {
        return errors.New("nome da impressora não configurado")
    }
    pName, err := utf16Ptr(printer)
    if err != nil { return err }
    var handle uintptr
    r1, _, e1 := procOpenPrinterW.Call(uintptr(unsafe.Pointer(pName)), uintptr(unsafe.Pointer(&handle)), 0)
    if r1 == 0 {
        return fmt.Errorf("OpenPrinter falhou para %q: %v", printer, e1)
    }
    defer procClosePrinter.Call(handle)

    if title == "" { title = appName }
    docName, _ := utf16Ptr(title)
    dataType, _ := utf16Ptr("RAW")
    info := docInfo1{DocName: docName, DataType: dataType}
    r1, _, e1 = procStartDocW.Call(handle, 1, uintptr(unsafe.Pointer(&info)))
    if r1 == 0 {
        return fmt.Errorf("StartDocPrinter falhou: %v", e1)
    }
    docStarted := true
    defer func() {
        if docStarted { procEndDoc.Call(handle) }
    }()

    r1, _, e1 = procStartPage.Call(handle)
    if r1 == 0 {
        return fmt.Errorf("StartPagePrinter falhou: %v", e1)
    }
    pageStarted := true
    defer func() {
        if pageStarted { procEndPage.Call(handle) }
    }()

    if len(data) == 0 { return errors.New("dados de impressão vazios") }
    var written uint32
    r1, _, e1 = procWritePrinter.Call(handle, uintptr(unsafe.Pointer(&data[0])), uintptr(uint32(len(data))), uintptr(unsafe.Pointer(&written)))
    if r1 == 0 {
        return fmt.Errorf("WritePrinter falhou: %v", e1)
    }
    if int(written) != len(data) {
        return fmt.Errorf("WritePrinter enviou %d de %d bytes", written, len(data))
    }

    r1, _, e1 = procEndPage.Call(handle)
    pageStarted = false
    if r1 == 0 { return fmt.Errorf("EndPagePrinter falhou: %v", e1) }
    r1, _, e1 = procEndDoc.Call(handle)
    docStarted = false
    if r1 == 0 { return fmt.Errorf("EndDocPrinter falhou: %v", e1) }
    return nil
}

func enumPrinters() ([]string, error) {
    const printerEnumLocal = 0x00000002
    const printerEnumConnections = 0x00000004
    flags := uintptr(printerEnumLocal | printerEnumConnections)
    var needed, returned uint32
    procEnumPrintersW.Call(flags, 0, 4, 0, 0, uintptr(unsafe.Pointer(&needed)), uintptr(unsafe.Pointer(&returned)))
    if needed == 0 { return []string{}, nil }
    buf := make([]byte, needed)
    r1, _, e1 := procEnumPrintersW.Call(flags, 0, 4, uintptr(unsafe.Pointer(&buf[0])), uintptr(needed), uintptr(unsafe.Pointer(&needed)), uintptr(unsafe.Pointer(&returned)))
    if r1 == 0 { return nil, fmt.Errorf("EnumPrinters falhou: %v", e1) }
    size := unsafe.Sizeof(printerInfo4{})
    names := make([]string, 0, returned)
    for i := uint32(0); i < returned; i++ {
        p := (*printerInfo4)(unsafe.Pointer(uintptr(unsafe.Pointer(&buf[0])) + uintptr(i)*size))
        if p.PrinterName != nil {
            name := syscall.UTF16ToString((*[1 << 20]uint16)(unsafe.Pointer(p.PrinterName))[:])
            if strings.TrimSpace(name) != "" { names = append(names, name) }
        }
    }
    sort.Strings(names)
    return names, nil
}

func (a *App) originAllowed(origin string) bool {
    if origin == "" { return true }
    for _, allowed := range a.cfg.AllowedOrigins {
        if strings.EqualFold(strings.TrimSpace(origin), strings.TrimSpace(allowed)) { return true }
    }
    return false
}

func (a *App) cors(w http.ResponseWriter, r *http.Request) bool {
    origin := r.Header.Get("Origin")
    if !a.originAllowed(origin) {
        http.Error(w, "origem não autorizada", http.StatusForbidden)
        return false
    }
    if origin != "" {
        w.Header().Set("Access-Control-Allow-Origin", origin)
        w.Header().Set("Vary", "Origin")
    }
    w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    w.Header().Set("Access-Control-Allow-Headers", "Content-Type, X-Noromak-Client")
    w.Header().Set("Access-Control-Allow-Private-Network", "true")
    w.Header().Set("Access-Control-Max-Age", "600")
    if r.Method == http.MethodOptions {
        w.WriteHeader(http.StatusNoContent)
        return false
    }
    return true
}

func writeJSON(w http.ResponseWriter, status int, data any) {
    w.Header().Set("Content-Type", "application/json; charset=utf-8")
    w.Header().Set("Cache-Control", "no-store")
    w.WriteHeader(status)
    _ = json.NewEncoder(w).Encode(data)
}

func (a *App) health(w http.ResponseWriter, r *http.Request) {
    if !a.cors(w, r) { return }
    printers, _ := enumPrinters()
    configuredFound := false
    for _, p := range printers {
        if strings.EqualFold(p, a.cfg.Printer) { configuredFound = true; break }
    }
    a.mu.Lock()
    printedCount := len(a.state.Printed)
    a.mu.Unlock()
    writeJSON(w, 200, JSONResponse{
        "ok": true,
        "service": appName,
        "version": appVersion,
        "printer": a.cfg.Printer,
        "printer_found": configuredFound,
        "port": a.cfg.Port,
        "uptime_seconds": int(time.Since(a.started).Seconds()),
        "printed_jobs_local": printedCount,
    })
}

func (a *App) printers(w http.ResponseWriter, r *http.Request) {
    if !a.cors(w, r) { return }
    list, err := enumPrinters()
    if err != nil {
        writeJSON(w, 500, JSONResponse{"ok": false, "error": err.Error()})
        return
    }
    writeJSON(w, 200, JSONResponse{"ok": true, "printers": list, "configured": a.cfg.Printer})
}

func (a *App) printHandler(w http.ResponseWriter, r *http.Request) {
    if !a.cors(w, r) { return }
    if r.Method != http.MethodPost {
        writeJSON(w, 405, JSONResponse{"ok": false, "error": "método não permitido"})
        return
    }
    if r.Header.Get("X-Noromak-Client") != "expedicao-web" {
        writeJSON(w, 403, JSONResponse{"ok": false, "error": "cliente local não autorizado"})
        return
    }
    r.Body = http.MaxBytesReader(w, r.Body, maxBodySize)
    defer r.Body.Close()
    dec := json.NewDecoder(r.Body)
    var req PrintRequest
    if err := dec.Decode(&req); err != nil {
        writeJSON(w, 400, JSONResponse{"ok": false, "error": "JSON inválido: " + err.Error()})
        return
    }
    req.JobID = strings.TrimSpace(req.JobID)
    if req.JobID == "" {
        writeJSON(w, 400, JSONResponse{"ok": false, "error": "job_id não informado"})
        return
    }
    if len(req.Payload) == 0 {
        writeJSON(w, 400, JSONResponse{"ok": false, "error": "payload ZPL vazio"})
        return
    }

    a.mu.Lock()
    if at, exists := a.state.Printed[req.JobID]; exists {
        a.mu.Unlock()
        writeJSON(w, 200, JSONResponse{"ok": true, "duplicate": true, "job_id": req.JobID, "printed_at": at})
        return
    }
    a.mu.Unlock()

    printer := strings.TrimSpace(req.Printer)
    if printer == "" { printer = a.cfg.Printer }
    var joined bytes.Buffer
    for _, part := range req.Payload {
        if strings.TrimSpace(part) == "" { continue }
        joined.WriteString(part)
        if !strings.HasSuffix(part, "\n") { joined.WriteByte('\n') }
    }
    if joined.Len() == 0 {
        writeJSON(w, 400, JSONResponse{"ok": false, "error": "payload ZPL sem conteúdo"})
        return
    }
    title := req.Title
    if title == "" {
        title = fmt.Sprintf("Noromak Pedido %s", req.OrderID)
    }
    started := time.Now()
    if err := rawPrint(printer, title, joined.Bytes()); err != nil {
        a.logger.Printf("ERRO job=%s pedido=%s printer=%q: %v", req.JobID, req.OrderID, printer, err)
        writeJSON(w, 500, JSONResponse{"ok": false, "error": err.Error(), "job_id": req.JobID})
        return
    }
    printedAt := time.Now().Format(time.RFC3339)
    a.mu.Lock()
    a.state.Printed[req.JobID] = printedAt
    _ = a.saveStateLocked()
    a.mu.Unlock()
    sum := sha256.Sum256(joined.Bytes())
    a.logger.Printf("OK job=%s pedido=%s marketplace=%s printer=%q bytes=%d sha256=%s duracao=%s", req.JobID, req.OrderID, req.Marketplace, printer, joined.Len(), hex.EncodeToString(sum[:8]), time.Since(started))
    writeJSON(w, 200, JSONResponse{
        "ok": true,
        "duplicate": false,
        "job_id": req.JobID,
        "printer": printer,
        "bytes": joined.Len(),
        "printed_at": printedAt,
    })
}

func (a *App) testHandler(w http.ResponseWriter, r *http.Request) {
    if !a.cors(w, r) { return }
    if r.Method != http.MethodPost {
        writeJSON(w, 405, JSONResponse{"ok": false, "error": "método não permitido"})
        return
    }
    if r.Header.Get("X-Noromak-Client") != "expedicao-web" {
        writeJSON(w, 403, JSONResponse{"ok": false, "error": "cliente local não autorizado"})
        return
    }
    zpl := fmt.Sprintf("^XA^PW800^LL430^FO45,45^A0N,42,42^FDNOROMAK PRINT SERVICE^FS^FO45,110^A0N,28,28^FDImpressao RAW direta - sem QZ Tray^FS^FO45,165^A0N,24,24^FD%s^FS^FO45,220^BY3^BCN,110,Y,N,N^FD123456789012^FS^XZ", time.Now().Format("02/01/2006 15:04:05"))
    if err := rawPrint(a.cfg.Printer, "Teste Noromak Print Service", []byte(zpl)); err != nil {
        a.logger.Printf("ERRO teste printer=%q: %v", a.cfg.Printer, err)
        writeJSON(w, 500, JSONResponse{"ok": false, "error": err.Error()})
        return
    }
    a.logger.Printf("OK teste printer=%q", a.cfg.Printer)
    writeJSON(w, 200, JSONResponse{"ok": true, "printer": a.cfg.Printer})
}

func (a *App) statusPage(w http.ResponseWriter, r *http.Request) {
    if r.URL.Path != "/" { http.NotFound(w, r); return }
    printers, _ := enumPrinters()
    found := false
    for _, p := range printers { if strings.EqualFold(p, a.cfg.Printer) { found = true } }
    status := "NÃO ENCONTRADA"
    color := "#b42318"
    if found { status = "PRONTA"; color = "#067647" }
    fmt.Fprintf(w, `<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>%s</title><style>body{font:16px Arial;background:#f4f7fb;color:#193b63;padding:32px}.card{max-width:720px;margin:auto;background:white;border-radius:16px;padding:28px;box-shadow:0 8px 30px #0002;border-top:6px solid #f7941d}code{background:#eef3f8;padding:4px 8px;border-radius:6px}h1{margin-top:0}.status{font-size:24px;font-weight:800;color:%s}</style><div class=card><h1>%s</h1><p>Versão %s</p><p class=status>%s</p><p>Impressora configurada: <code>%s</code></p><p>Porta local: <code>127.0.0.1:%d</code></p><p>Log: <code>%s</code></p><p>Esta página pode ser fechada. O serviço permanece em segundo plano.</p></div></html>`, html.EscapeString(appName), color, html.EscapeString(appName), appVersion, status, html.EscapeString(a.cfg.Printer), a.cfg.Port, html.EscapeString(a.logPath))
}

func main() {
    dir := localDataDir()
    if err := os.MkdirAll(dir, 0700); err != nil { panic(err) }
    cfgPath := filepath.Join(dir, "config.json")
    statePath := filepath.Join(dir, "state.json")
    logPath := filepath.Join(dir, "service.log")
    logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
    if err != nil { panic(err) }
    defer logFile.Close()
    logger := log.New(logFile, "", log.Ldate|log.Ltime|log.Lmicroseconds)
    cfg, err := loadConfig(cfgPath)
    if err != nil {
        logger.Printf("ERRO config: %v", err)
        cfg = defaultConfig()
    }
    app := &App{cfg: cfg, cfgPath: cfgPath, statePath: statePath, logPath: logPath, started: time.Now(), state: loadState(statePath), logger: logger}

    mux := http.NewServeMux()
    mux.HandleFunc("/", app.statusPage)
    mux.HandleFunc("/health", app.health)
    mux.HandleFunc("/printers", app.printers)
    mux.HandleFunc("/print", app.printHandler)
    mux.HandleFunc("/test", app.testHandler)

    addr := fmt.Sprintf("127.0.0.1:%d", cfg.Port)
    listener, err := net.Listen("tcp4", addr)
    if err != nil {
        logger.Printf("ERRO listen %s: %v", addr, err)
        return
    }
    logger.Printf("INICIO %s %s em %s printer=%q", appName, appVersion, addr, cfg.Printer)
    server := &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 35 * time.Second, WriteTimeout: 35 * time.Second, IdleTimeout: 60 * time.Second}
    if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
        logger.Printf("ERRO servidor: %v", err)
    }
}
