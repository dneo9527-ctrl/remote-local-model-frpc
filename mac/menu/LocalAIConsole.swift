// Native menu for the existing loopback console. No keys or upstream requests.
import AppKit
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

    func request(_ path: String, token: String? = nil) -> [String: Any]? {
        var request = URLRequest(url: URL(string: base + path)!)
        let timeout: TimeInterval = token == nil ? 4 : 75
        request.timeoutInterval = timeout
        if let token = token {
            request.httpMethod = "POST"
            request.httpBody = Data("{}".utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
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
        return .running(LocalSession(instance: instance, csrf: csrf))
    }
}

final class ConsoleMenu: NSObject, NSApplicationDelegate, NSMenuDelegate {
    let settings: MenuSettings
    let client: LocalClient
    let queue = DispatchQueue(label: "local.ai.console.lifecycle", qos: .utility)
    var statusItem: NSStatusItem!
    var stateItem = NSMenuItem(title: "正在启动…", action: nil, keyEquivalent: "")
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
        NSApp.mainMenu = main
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.refresh() }
        start(openPage: true)
    }

    func menuWillOpen(_ menu: NSMenu) { refresh() }

    func display(_ title: String, alive: Bool, busy: Bool = false) {
        stateItem.title = title
        running = alive
        statusItem.button?.alphaValue = alive ? 1 : 0.5
        statusItem.button?.toolTip = "本地 AI 控制台 · " + title
        statusItem.button?.setAccessibilityValue(title)
        openItem.isEnabled = !busy
        startItem.isHidden = alive
        startItem.isEnabled = !busy
        quitItem.isEnabled = !busy
        mainQuitItem.isEnabled = !busy
        // Diagnostic state contains no credentials, request bodies or private config.
        let record: [String: Any] = ["pid": ProcessInfo.processInfo.processIdentifier,
                                   "backend_running": alive, "state": title,
                                   "status_item_visible": statusItem.isVisible,
                                   "icon_loaded": statusItem.button?.image != nil]
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
            if case .running(let session) = state { self.attachedInstance = session.instance }
            DispatchQueue.main.async {
                guard !self.quitting, !self.starting else { return }
                switch state {
                case .running: self.display("后台运行中", alive: true)
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
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // Reopening an already running accessory app reveals its existing menu.
        sender.activate(ignoringOtherApps: true)
        statusItem.button?.performClick(nil)
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
            if case .running(let session) = state { self.attachedInstance = session.instance }
            let childAlive = self.child?.isRunning == true
            DispatchQueue.main.async {
                self.starting = false
                switch state {
                case .running:
                    self.display("后台运行中", alive: true)
                    if openPage { NSWorkspace.shared.open(URL(string: self.client.base)!) }
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
