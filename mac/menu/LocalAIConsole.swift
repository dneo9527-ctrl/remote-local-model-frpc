// Native menu for the existing loopback console. No keys or upstream requests.
import AppKit
import WebKit
import Foundation
import Darwin

struct MenuSettings: Decodable {
    let python: String
    let source: String
    let config: String
    let state: String
    let runtime: String
    let port: Int
}

struct LocalSession {
    let instance: String
    let csrf: String
    let supportsPassiveStatus: Bool
}

enum ConsoleState {
    case stopped, running(LocalSession), unavailable
}

// The app's server → computer mark, simplified for the 22-point menu bar.
// Template rendering lets macOS choose white/black for the current appearance.
func connectionMenuImage() -> NSImage {
    let image = NSImage(size: NSSize(width: 22, height: 18), flipped: false) { _ in
        NSColor.black.setStroke()
        NSColor.black.setFill()
        func line(_ points: [NSPoint], width: CGFloat = 1.45) {
            let path = NSBezierPath()
            path.move(to: points[0])
            points.dropFirst().forEach { path.line(to: $0) }
            path.lineWidth = width
            path.lineCapStyle = .round
            path.lineJoinStyle = .round
            path.stroke()
        }
        let server = NSBezierPath(roundedRect: NSRect(x: 1.1, y: 2.2, width: 5.7, height: 13.4),
                                  xRadius: 1.2, yRadius: 1.2)
        server.lineWidth = 1.35
        server.stroke()
        line([NSPoint(x: 2.8, y: 12.4), NSPoint(x: 5.1, y: 12.4)], width: 1)
        line([NSPoint(x: 2.8, y: 10.2), NSPoint(x: 5.1, y: 10.2)], width: 1)
        NSBezierPath(ovalIn: NSRect(x: 3.25, y: 4.5, width: 1.3, height: 1.3)).fill()
        let screen = NSBezierPath(roundedRect: NSRect(x: 12.8, y: 5.5, width: 8, height: 7.8),
                                  xRadius: 1, yRadius: 1)
        screen.lineWidth = 1.45
        screen.stroke()
        line([NSPoint(x: 16.8, y: 5.1), NSPoint(x: 16.8, y: 2.6)])
        line([NSPoint(x: 12, y: 2.6), NSPoint(x: 21.1, y: 2.6)])
        line([NSPoint(x: 8.3, y: 9), NSPoint(x: 13.9, y: 9)])
        line([NSPoint(x: 11.7, y: 11.1), NSPoint(x: 13.9, y: 9), NSPoint(x: 11.7, y: 6.9)])
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "本地 AI 控制台：服务器连接电脑"
    return image
}

// Keep both local pages alive in one panel, including streams and unsent text.
// Closing the panel hides it; only All Quit stops the owned connections.
final class ConsolePanel: NSObject, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate {
    let baseURL: URL
    private(set) var window: NSWindow?
    private var webViews: [String: WKWebView] = [:]
    private var loadedPages: Set<String> = []
    private var failedPages: Set<String> = []
    private(set) var activePage = "/"
    private var instance: String?
    var visibilityChanged: (() -> Void)?

    init(baseURL: URL) { self.baseURL = baseURL }

    private func isLocalPage(_ url: URL) -> Bool {
        url.scheme == baseURL.scheme && url.host == baseURL.host && url.port == baseURL.port &&
            url.user == nil && url.password == nil && ["/", "/chat"].contains(url.path)
    }

    func show(instance current: String) {
        if window == nil {
            let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1000, height: 660),
                                 styleMask: [.titled, .closable, .miniaturizable, .resizable],
                                 backing: .buffered, defer: false)
            panel.title = "本地 AI 控制台"
            panel.minSize = NSSize(width: 560, height: 440)
            panel.isReleasedWhenClosed = false
            panel.collectionBehavior = [.moveToActiveSpace]
            panel.contentView = NSView()
            panel.delegate = self
            if !panel.setFrameUsingName("LocalAIConsolePanel") { panel.center() }
            panel.setFrameAutosaveName("LocalAIConsolePanel")
            window = panel
        }
        updateInstance(current)
        displayPage(activePage)
        if window?.isMiniaturized == true { window?.deminiaturize(nil) }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        visibilityChanged?()
    }

    func updateInstance(_ current: String) {
        // A backend change invalidates both CSRF sessions. Ordinary navigation,
        // reopen and minimize never reload either already-loaded page.
        guard window != nil, instance != current else { return }
        instance = current
        resetPages()
        activePage = "/"
        displayPage(activePage)
    }

    private func resetPages() {
        for view in webViews.values {
            view.navigationDelegate = nil
            view.uiDelegate = nil
            view.stopLoading()
            view.removeFromSuperview()
        }
        webViews.removeAll()
        loadedPages.removeAll()
        failedPages.removeAll()
        guard let container = window?.contentView else { return }
        let store = WKWebsiteDataStore.nonPersistent()
        for path in ["/", "/chat"] {
            let configuration = WKWebViewConfiguration()
            configuration.websiteDataStore = store
            let view = WKWebView(frame: .zero, configuration: configuration)
            view.navigationDelegate = self
            view.uiDelegate = self
            view.translatesAutoresizingMaskIntoConstraints = false
            view.isHidden = true
            container.addSubview(view)
            NSLayoutConstraint.activate([
                view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
                view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                view.topAnchor.constraint(equalTo: container.topAnchor),
                view.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            ])
            webViews[path] = view
        }
    }

    private func displayPage(_ path: String) {
        guard let selected = webViews[path] else { return }
        activePage = path
        // Keep the other WKWebView attached: hiding a page must not cancel its
        // fetch, stream reader, conversation state or textarea contents.
        for (page, view) in webViews { view.isHidden = page != path }
        if !loadedPages.contains(path) || failedPages.contains(path) {
            loadedPages.insert(path)
            failedPages.remove(path)
            var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
            components.path = path
            selected.load(URLRequest(url: components.url!))
        }
        visibilityChanged?()
    }

    private func page(for view: WKWebView) -> String? {
        webViews.first(where: { $0.value === view })?.key
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        visibilityChanged?()
        return false
    }

    func windowDidMiniaturize(_ notification: Notification) { visibilityChanged?() }
    func windowDidDeminiaturize(_ notification: Notification) { visibilityChanged?() }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if isLocalPage(url) {
            if action.navigationType == .linkActivated || action.targetFrame == nil ||
                page(for: webView) != url.path {
                decisionHandler(.cancel)
                displayPage(url.path)
            } else { decisionHandler(.allow) }
        } else {
            // Only an explicit link click can leave the local panel.
            if action.navigationType == .linkActivated, ["http", "https"].contains(url.scheme ?? ""),
               url.user == nil, url.password == nil {
                NSWorkspace.shared.open(url)
            }
            decisionHandler(.cancel)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        nil  // Local target=_blank links are handled above in the same panel.
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard let origin = frame.request.url, isLocalPage(origin), page(for: webView) != nil,
              let panel = window else { completionHandler(false); return }
        // WKWebView does not supply a browser confirmation UI by itself. Keep
        // JavaScript paused until this native sheet receives the user's answer.
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = "请确认操作"
        alert.informativeText = message
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消").keyEquivalent = "\u{1b}"
        alert.beginSheetModal(for: panel) { response in
            completionHandler(response == .alertFirstButtonReturn)
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        guard let origin = frame.request.url, isLocalPage(origin), page(for: webView) != nil,
              let panel = window else { completionHandler(); return }
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "本地 AI 控制台"
        alert.informativeText = message
        alert.addButton(withTitle: "好")
        alert.beginSheetModal(for: panel) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let picker = NSOpenPanel()
        picker.canChooseFiles = true
        picker.canChooseDirectories = false
        picker.allowsMultipleSelection = parameters.allowsMultipleSelection
        let finished: (NSApplication.ModalResponse) -> Void = { result in
            completionHandler(result == .OK ? picker.urls : nil)
        }
        if let window = window { picker.beginSheetModal(for: window, completionHandler: finished) }
        else { picker.begin(completionHandler: finished) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let path = page(for: webView) { failedPages.remove(path) }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled, let path = page(for: webView) {
            failedPages.insert(path)
        }
    }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled, let path = page(for: webView) {
            failedPages.insert(path)
        }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        if let path = page(for: webView) { failedPages.insert(path) }
    }
}

struct ConnectionSummary {
    let ollama: String
    let sub2api: String

    static let unknown = ConnectionSummary(ollama: "Windows 模型：连接状态未知",
                                            sub2api: "Sub2API：连接状态未知")

    init(ollama: String, sub2api: String) {
        self.ollama = ollama
        self.sub2api = sub2api
    }

    init(status: [String: Any], now: Date = Date()) {
        if let model = status["ollama"] as? [String: Any],
           let running = model["running"] as? Bool {
            if model["ownership"] as? String == "external" ||
                (running && model["managed"] as? Bool == false) {
                ollama = "Windows 模型：外部连接 · 未接管"
            } else {
                ollama = running ? "Windows 模型：本地连接已开启" : "Windows 模型：本地连接已关闭"
            }
        } else { ollama = Self.unknown.ollama }

        guard let service = status["sub2api"] as? [String: Any] else {
            sub2api = "Sub2API：未配置连接"
            return
        }
        let local: String
        if service["configured"] as? Bool == false {
            local = "未配置连接"
        } else if service["mode"] as? String == "direct" {
            local = "直连地址"
        } else if service["ownership"] as? String == "external" {
            local = "外部连接 · 未接管"
        } else if let running = service["running"] as? Bool {
            local = running ? (service["listener"] as? Bool == true ? "本地连接已开启" : "本地连接启动中")
                : "本地连接已关闭"
        } else { local = "连接状态未知" }
        let remote = service["remote"] as? [String: Any] ?? [:]
        let state = remote["state"] as? String ?? "unchecked"
        guard state == "ok" || state == "error" || state == "stale" else {
            sub2api = "Sub2API：\(local) · API 未检查"
            return
        }
        let result = state == "stale" ? "上次检查已过期" : state == "ok" ? "上次检查通过" : "上次检查未通过"
        let checked: Date?
        if let raw = remote["checked_at"] as? String {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: raw) { checked = date }
            else {
                formatter.formatOptions = [.withInternetDateTime]
                checked = formatter.date(from: raw)
            }
        } else { checked = nil }
        if let checked = checked {
            let clock = DateFormatter()
            clock.dateFormat = "HH:mm"
            let ttl = max(0, remote["ttl_seconds"] as? Double ?? 300)
            let stale = state == "stale" || remote["stale"] as? Bool == true || now.timeIntervalSince(checked) > ttl || checked > now
            let label = stale ? "上次检查已过期" : result
            sub2api = "Sub2API：\(local) · \(label) \(clock.string(from: checked))"
        } else {
            sub2api = "Sub2API：\(local) · \(result) · 时间未知"
        }
    }
}

final class LocalClient: NSObject, URLSessionTaskDelegate {
    let settings: MenuSettings
    var base: String { "http://127.0.0.1:\(settings.port)" }
    lazy var session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]
        config.timeoutIntervalForRequest = 2
        config.timeoutIntervalForResource = 75
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()
    init(_ settings: MenuSettings) { self.settings = settings }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)  // Only the configured loopback endpoint.
    }

    func request(_ path: String, token: String? = nil, method: String? = nil) -> [String: Any]? {
        var request = URLRequest(url: URL(string: base + path)!)
        let verb = method ?? (token == nil ? "GET" : "POST")
        let timeout: TimeInterval = verb == "GET" ? 4 : 75
        request.timeoutInterval = timeout
        request.httpMethod = verb
        if let token = token {
            if verb == "POST" {
                request.httpBody = Data("{}".utf8)
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            }
            request.setValue(token, forHTTPHeaderField: "X-Local-CSRF")
        }
        let ready = DispatchSemaphore(value: 0)
        var result: [String: Any]?
        let task = session.dataTask(with: request) { data, response, _ in
            defer { ready.signal() }
            guard let response = response as? HTTPURLResponse, response.statusCode == 200,
                  let data = data, data.count < 65536 else { return }
            result = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        }
        task.resume()
        if ready.wait(timeout: .now() + timeout) == .timedOut { task.cancel(); return nil }
        return result
    }

    func occupied() -> Bool {
        let fd = socket(AF_INET, SOCK_STREAM, 0)
        guard fd >= 0 else { return true }
        defer { close(fd) }
        var reuse: Int32 = 1
        _ = setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &reuse, socklen_t(MemoryLayout<Int32>.size))
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = UInt16(settings.port).bigEndian
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        return withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) != 0
            }
        }
    }

    func inspect() -> ConsoleState {
        guard let reply = request("/api/session") else {
            return occupied() ? .unavailable : .stopped
        }
        let recordURL = URL(fileURLWithPath: settings.runtime).appendingPathComponent("data/server.json")
        guard reply["service"] as? String == "local-ai-console",
              let instance = reply["instance"] as? String, !instance.isEmpty,
              let csrf = reply["csrf"] as? String, !csrf.isEmpty,
              let bytes = try? Data(contentsOf: recordURL),
              let record = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any],
              record["url"] as? String == base, record["instance"] as? String == instance else {
            return .unavailable
        }
        return .running(LocalSession(instance: instance, csrf: csrf,
                                     supportsPassiveStatus: reply["passive_status"] as? Bool == true))
    }

    func connectionSummary(for session: LocalSession) -> ConnectionSummary {
        // Old servers may ignore query parameters and perform remote probes.
        // Only a backend explicitly advertising passive status may be queried.
        guard session.supportsPassiveStatus,
              let status = request("/api/status?passive=1", token: session.csrf, method: "GET"),
              status["passive"] as? Bool == true,
              status["instance"] as? String == session.instance else { return .unknown }
        let recordURL = URL(fileURLWithPath: settings.runtime).appendingPathComponent("data/server.json")
        guard let bytes = try? Data(contentsOf: recordURL),
              let record = (try? JSONSerialization.jsonObject(with: bytes)) as? [String: Any],
              record["url"] as? String == base,
              record["instance"] as? String == session.instance else { return .unknown }
        return ConnectionSummary(status: status)
    }
}

final class ConsoleMenu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let settings: MenuSettings
    let client: LocalClient
    let panel: ConsolePanel
    let queue = DispatchQueue(label: "local.ai.console.lifecycle", qos: .utility)
    var statusItem: NSStatusItem!
    var stateItem = NSMenuItem(title: "正在启动…", action: nil, keyEquivalent: "")
    var ollamaItem = NSMenuItem(title: ConnectionSummary.unknown.ollama, action: nil, keyEquivalent: "")
    var sub2apiItem = NSMenuItem(title: ConnectionSummary.unknown.sub2api, action: nil, keyEquivalent: "")
    var openItem: NSMenuItem!
    var startItem: NSMenuItem!
    var quitItem: NSMenuItem!
    var mainQuitItem: NSMenuItem!
    var timer: Timer?
    var child: Process?
    var attachedInstance: String?  // Accessed only on the serial lifecycle queue.
    var lockFD: Int32 = -1
    var quitting = false
    var mayTerminate = false
    var starting = false
    var running = false

    func completedQuit(for instance: String?) -> Bool {
        guard let instance = instance else { return false }
        let path = URL(fileURLWithPath: settings.runtime).appendingPathComponent("data/shutdown.json")
        guard let data = try? Data(contentsOf: path),
              let record = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return false }
        return record["instance"] as? String == instance && record["complete"] as? Bool == true
    }

    init(_ settings: MenuSettings) {
        self.settings = settings
        self.client = LocalClient(settings)
        self.panel = ConsolePanel(baseURL: URL(string: "http://127.0.0.1:\(settings.port)/")!)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let directory = URL(fileURLWithPath: settings.runtime).appendingPathComponent("data")
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                  attributes: [.posixPermissions: 0o700])
        lockFD = Darwin.open(directory.appendingPathComponent("menu.lock").path, O_CREAT | O_RDWR, 0o600)
        guard lockFD >= 0, flock(lockFD, LOCK_EX | LOCK_NB) == 0 else {
            DistributedNotificationCenter.default().postNotificationName(
                NSNotification.Name("local.ai.console.open"), object: settings.runtime, deliverImmediately: true)
            mayTerminate = true
            NSApp.terminate(nil)
            return
        }
        DistributedNotificationCenter.default().addObserver(self, selector: #selector(reopenNotice(_:)),
            name: NSNotification.Name("local.ai.console.open"), object: settings.runtime)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = connectionMenuImage()
        statusItem.button?.setAccessibilityLabel("本地 AI 控制台")
        let menu = NSMenu()
        menu.autoenablesItems = false
        menu.delegate = self
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        for item in [ollamaItem, sub2apiItem] {
            item.isEnabled = false
            menu.addItem(item)
        }
        menu.addItem(.separator())
        openItem = NSMenuItem(title: "打开控制台…", action: #selector(openConsole), keyEquivalent: "o")
        startItem = NSMenuItem(title: "启动控制台", action: #selector(startConsole), keyEquivalent: "")
        quitItem = NSMenuItem(title: "全部退出", action: #selector(quitConsole), keyEquivalent: "q")
        for item in [openItem!, startItem!] { item.target = self; menu.addItem(item) }
        menu.addItem(.separator())
        let note = NSMenuItem(title: "停止 Ollama、Sub2API 和本机后台", action: nil, keyEquivalent: "")
        note.isEnabled = false
        menu.addItem(note)
        quitItem.target = self
        menu.addItem(quitItem)
        statusItem.menu = menu
        panel.visibilityChanged = { [weak self] in self?.writeDiagnosticState() }
        // Standard app menu also supports keyboard quit and accessibility.
        let main = NSMenu()
        let appItem = NSMenuItem(title: "本地 AI 控制台", action: nil, keyEquivalent: "")
        let appMenu = NSMenu(title: "本地 AI 控制台")
        appMenu.autoenablesItems = false
        let mainOpen = NSMenuItem(title: "打开控制台…", action: #selector(openConsole), keyEquivalent: "o")
        mainOpen.target = self
        appMenu.addItem(mainOpen)
        mainQuitItem = NSMenuItem(title: "全部退出", action: #selector(quitConsole), keyEquivalent: "q")
        mainQuitItem.target = self
        appMenu.addItem(mainQuitItem)
        appItem.submenu = appMenu
        main.addItem(appItem)
        let fileItem = NSMenuItem(title: "文件", action: nil, keyEquivalent: "")
        let fileMenu = NSMenu(title: "文件")
        // Use the responder chain so ⌘W closes only the active console window.
        fileMenu.addItem(NSMenuItem(title: "关闭窗口", action: #selector(NSWindow.performClose(_:)),
                                    keyEquivalent: "w"))
        fileItem.submenu = fileMenu
        main.addItem(fileItem)
        let editItem = NSMenuItem(title: "编辑", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "编辑")
        for (title, action, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"),
                                     ("复制", "copy:", "c"), ("粘贴", "paste:", "v"),
                                     ("全选", "selectAll:", "a")] {
            editMenu.addItem(NSMenuItem(title: title, action: Selector(action), keyEquivalent: key))
        }
        editItem.submenu = editMenu
        main.addItem(editItem)
        let windowItem = NSMenuItem(title: "窗口", action: nil, keyEquivalent: "")
        let windowMenu = NSMenu(title: "窗口")
        windowMenu.addItem(NSMenuItem(title: "最小化", action: #selector(NSWindow.performMiniaturize(_:)),
                                      keyEquivalent: "m"))
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.windowsMenu = windowMenu
        NSApp.mainMenu = main
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.refresh() }
        start(openPage: true)
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    func display(_ title: String, alive: Bool, busy: Bool = false,
                 summary: ConnectionSummary = .unknown) {
        stateItem.title = title
        ollamaItem.title = summary.ollama
        sub2apiItem.title = summary.sub2api
        running = alive
        statusItem.button?.alphaValue = alive ? 1 : 0.5
        statusItem.button?.toolTip = "本地 AI 控制台 · " + title
        statusItem.button?.setAccessibilityValue(title)
        openItem.isEnabled = !busy
        startItem.isHidden = alive
        startItem.isEnabled = !busy
        quitItem.isEnabled = !busy
        mainQuitItem.isEnabled = !busy
        writeDiagnosticState()
    }

    func writeDiagnosticState() {
        // Diagnostic state contains no credentials, request bodies or private config.
        let record: [String: Any] = ["pid": ProcessInfo.processInfo.processIdentifier,
                                   "backend_running": running, "state": stateItem.title,
                                   "ollama_summary": ollamaItem.title,
                                   "sub2api_summary": sub2apiItem.title,
                                   "status_item_visible": statusItem.isVisible,
                                   "icon_loaded": statusItem.button?.image != nil,
                                   "console_window_number": panel.window?.windowNumber ?? 0,
                                   "console_window_visible": panel.window?.isVisible ?? false,
                                   "console_window_minimized": panel.window?.isMiniaturized ?? false,
                                   "console_page": panel.activePage]
        let path = URL(fileURLWithPath: settings.runtime).appendingPathComponent("data/menu-state.json")
        if let data = try? JSONSerialization.data(withJSONObject: record) {
            try? data.write(to: path, options: .atomic)
            try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
        }
    }

    func refresh() {
        guard !quitting, !starting else { return }
        queue.async {
            let state = self.client.inspect()
            let childAlive = self.child?.isRunning == true
            let fullyStopped = self.completedQuit(for: self.attachedInstance)
            var summary = ConnectionSummary.unknown
            if case .running(let session) = state {
                self.attachedInstance = session.instance
                summary = self.client.connectionSummary(for: session)
            }
            DispatchQueue.main.async {
                guard !self.quitting, !self.starting else { return }
                switch state {
                case .running(let session):
                    self.panel.updateInstance(session.instance)
                    self.display("后台运行中 · API 状态独立", alive: true, summary: summary)
                case .stopped:
                    if fullyStopped && !childAlive {
                        self.mayTerminate = true
                        NSApp.terminate(nil)
                        return
                    }
                    self.display(childAlive ? "后台仍在启动，请稍候" : "后台已停止", alive: false)
                    self.startItem.isEnabled = !childAlive
                case .unavailable: self.display("服务身份未确认，请查看日志", alive: false)
                }
            }
        }
    }

    @objc func openConsole() { start(openPage: true) }
    @objc func startConsole() { start(openPage: false) }
    @objc func reopenNotice(_ notification: Notification) { openConsole() }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openConsole()
        return false
    }

    func start(openPage: Bool) {
        guard !starting, !quitting else { return }
        starting = true
        display("正在启动…", alive: false, busy: true)
        queue.async {
            var state = self.client.inspect()
            if case .stopped = state, self.child?.isRunning != true {
                do {
                    let process = Process()
                    process.executableURL = URL(fileURLWithPath: self.settings.python)
                    process.arguments = ["-B", self.settings.source + "/tools/launch.py",
                        "--config", self.settings.config, "--state", self.settings.state,
                        "--foreground", "--owner-pid", String(ProcessInfo.processInfo.processIdentifier)]
                    let logs = URL(fileURLWithPath: self.settings.runtime).appendingPathComponent("logs")
                    try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true,
                                                            attributes: [.posixPermissions: 0o700])
                    let log = logs.appendingPathComponent("menu-startup.log")
                    if !FileManager.default.fileExists(atPath: log.path) {
                        FileManager.default.createFile(atPath: log.path, contents: nil,
                                                       attributes: [.posixPermissions: 0o600])
                    }
                    let out = try FileHandle(forWritingTo: log)
                    try out.seekToEnd()
                    process.standardOutput = out
                    process.standardError = out
                    process.standardInput = FileHandle.nullDevice
                    process.terminationHandler = { [weak owner = self] _ in
                        DispatchQueue.main.async { owner?.refresh() }
                    }
                    try process.run()
                    self.child = process
                    try? out.close()
                    let deadline = Date().addingTimeInterval(10)
                    repeat {
                        Thread.sleep(forTimeInterval: 0.15)
                        state = self.client.inspect()
                        if case .running = state { break }
                    } while process.isRunning && Date() < deadline
                } catch { state = .unavailable }
            }
            var summary = ConnectionSummary.unknown
            if case .running(let session) = state {
                self.attachedInstance = session.instance
                summary = self.client.connectionSummary(for: session)
            }
            let childAlive = self.child?.isRunning == true
            DispatchQueue.main.async {
                self.starting = false
                switch state {
                case .running(let session):
                    self.display("后台运行中 · API 状态独立", alive: true, summary: summary)
                    if openPage { self.panel.show(instance: session.instance) }
                case .stopped:
                    self.display(childAlive ? "后台仍在启动，请稍候" : "启动未完成，请查看本机日志", alive: false)
                    self.startItem.isEnabled = !childAlive
                case .unavailable: self.display("服务身份未确认，请查看日志", alive: false)
                }
            }
        }
    }

    @objc func quitConsole() { NSApp.terminate(nil) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if mayTerminate { return .terminateNow }
        if quitting { return .terminateLater }
        quitting = true
        display("正在退出…", alive: running, busy: true)
        queue.async {
            let initial = self.client.inspect()
            if case .unavailable = initial, self.attachedInstance == nil, self.child?.isRunning != true {
                // This menu never owned or attached to the unknown service.
                // Closing its own icon is safe; do not send shutdown or signals.
                DispatchQueue.main.async {
                    self.mayTerminate = true
                    NSApp.reply(toApplicationShouldTerminate: true)
                }
                return
            }
            var expected = self.attachedInstance
            if case .running(let session) = initial {
                expected = session.instance
                _ = self.client.request("/api/shutdown", token: session.csrf)
            } else if self.child?.isRunning == true {
                // Only signal the Process this menu directly spawned, never a PID file.
                self.child?.terminate()
            }
            let deadline = Date().addingTimeInterval(12)
            var stopped = false
            repeat {
                let state = self.client.inspect()
                let originalStillThere: Bool
                switch state {
                case .running(let current):
                    originalStillThere = expected == nil || expected == current.instance
                case .stopped: originalStillThere = false
                case .unavailable: originalStillThere = true // Unknown is not an exit receipt.
                }
                stopped = self.child?.isRunning != true && !originalStillThere &&
                    (expected == nil || self.completedQuit(for: expected))
                if stopped { break }
                Thread.sleep(forTimeInterval: 0.2)
            } while Date() < deadline
            DispatchQueue.main.async {
                if stopped {
                    self.mayTerminate = true
                    NSApp.reply(toApplicationShouldTerminate: true)
                } else {
                    self.quitting = false
                    self.display("后台尚未退出，请重试", alive: true)
                    NSApp.reply(toApplicationShouldTerminate: false)
                }
            }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        if statusItem != nil { NSStatusBar.system.removeStatusItem(statusItem) }
        if lockFD >= 0 { close(lockFD) }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
do {
    guard let resource = Bundle.main.url(forResource: "MenuSettings", withExtension: "json") else {
        throw CocoaError(.fileNoSuchFile)
    }
    let settings = try JSONDecoder().decode(MenuSettings.self, from: Data(contentsOf: resource))
    guard (1024...65535).contains(settings.port),
          [settings.python, settings.source, settings.config, settings.state, settings.runtime].allSatisfy({ $0.hasPrefix("/") }) else {
        throw CocoaError(.fileReadCorruptFile)
    }
    let delegate = ConsoleMenu(settings)
    app.delegate = delegate
    withExtendedLifetime(delegate) { app.run() }
} catch {
    let alert = NSAlert()
    alert.messageText = "本地 AI 控制台未能启动"
    alert.informativeText = "菜单栏启动资料缺失或无效，请恢复本机安装。原有连接资料未更改。"
    alert.runModal()
}
