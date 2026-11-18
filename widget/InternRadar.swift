// Intern Radar — a desktop widget that shows dashboard/index.html, runs the job-board sweep every
// 5 hours (and as soon as the Mac is awake and online if a sweep is overdue), and queues links
// pasted into it for Claude to research (data/inbox.json).
// Build with widget/build.sh. The project root is two levels above the .app bundle.

import Cocoa
import EventKit
import Network
import UserNotifications
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate,
                         WKScriptMessageHandler, UNUserNotificationCenterDelegate {
    let root = Bundle.main.bundleURL.deletingLastPathComponent().deletingLastPathComponent()
    let sweepInterval: TimeInterval = 5 * 3600
    let retryAfterFailure: TimeInterval = 15 * 60
    let compactHeight: CGFloat = 112
    let defaults = UserDefaults.standard

    var window: NSWindow!
    var web: WKWebView!
    var statusItem: NSStatusItem?
    var sweepMenuItem: NSMenuItem?
    var summaryMenuItem: NSMenuItem?
    var floatMenuItem: NSMenuItem!
    var collapseButton: NSButton!
    let monitor = NWPathMonitor()
    var online = false
    var sweeping = false
    var lastAttempt = Date.distantPast
    var loadedVersion = Date.distantPast
    var sweepFailed = false
    var napGuard: NSObjectProtocol?
    let events = EKEventStore()
    var remindersAllowed = false
    let maxBannersPerDay = 3

    var page: URL { root.appendingPathComponent("dashboard/index.html") }
    var dataDir: URL { root.appendingPathComponent("data") }
    var logFile: URL { dataDir.appendingPathComponent("sweep_log.json") }

    // MARK: launch

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMainMenu()
        buildWindow()
        buildStatusMenu()
        monitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                self?.online = path.status == .satisfied
                self?.sweepIfDue()
            }
        }
        monitor.start(queue: DispatchQueue.global(qos: .utility))
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(woke), name: NSWorkspace.didWakeNotification, object: nil)
        Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.tick() }
        // Keep the one-minute timer honest while the widget sits behind other windows.
        napGuard = ProcessInfo.processInfo.beginActivity(options: [.userInitiatedAllowingIdleSystemSleep],
                                                         reason: "Intern Radar schedule and alerts")
        setUpNotifications()
        requestReminders()
        reload()
    }

    /// An Edit menu is what makes ⌘V / ⌘C / ⌘A work inside the link box.
    func buildMainMenu() {
        let main = NSMenu()
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    func buildWindow() {
        let screen = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        let size = NSSize(width: 400, height: min(560, screen.height - 32))
        let origin = NSPoint(x: screen.maxX - size.width - 16, y: screen.maxY - size.height - 16)
        // Titled + full-size content: native rounded corners, drag by the top strip, resize by any edge.
        window = NSWindow(contentRect: NSRect(origin: origin, size: size),
                          styleMask: [.titled, .fullSizeContentView, .resizable],
                          backing: .buffered, defer: false)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        for kind in [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton] {
            window.standardWindowButton(kind)?.isHidden = true
        }
        window.delegate = self
        window.isReleasedWhenClosed = false
        window.minSize = NSSize(width: 330, height: compactHeight)
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        applyLevel()

        let glass = NSVisualEffectView()
        glass.material = .popover
        glass.blendingMode = .behindWindow
        glass.state = .active

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "radar")
        web = WKWebView(frame: NSRect(origin: .zero, size: size), configuration: config)
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        web.autoresizingMask = [.width, .height]
        glass.addSubview(web)
        window.contentView = glass
        web.frame = glass.bounds

        // Collapse / expand button in the top-right corner of the drag strip.
        collapseButton = NSButton(title: "", target: self, action: #selector(toggleCompact))
        collapseButton.isBordered = false
        collapseButton.contentTintColor = .secondaryLabelColor
        collapseButton.frame = NSRect(x: 0, y: 0, width: 34, height: 28)
        let accessory = NSTitlebarAccessoryViewController()
        accessory.view = collapseButton
        accessory.layoutAttribute = .trailing
        window.addTitlebarAccessoryViewController(accessory)

        // Restores the position and size from the last run, if any.
        window.setFrameAutosaveName("InternRadarWidget")
        refreshCollapseIcon()
        window.orderFront(nil)
    }

    func buildStatusMenu() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem = item
        item.button?.title = "◎ Radar"
        let menu = NSMenu()
        summaryMenuItem = menu.addItem(withTitle: "Nothing waiting on you", action: #selector(showWidget), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Show / hide widget", action: #selector(toggleWindow), keyEquivalent: "")
        menu.addItem(withTitle: "Collapse / expand", action: #selector(toggleCompact), keyEquivalent: "")
        floatMenuItem = menu.addItem(withTitle: "Keep above other windows", action: #selector(toggleFloat), keyEquivalent: "")
        menu.addItem(withTitle: "Move to top-right corner", action: #selector(resetPosition), keyEquivalent: "")
        menu.addItem(.separator())
        sweepMenuItem = menu.addItem(withTitle: "Sweep now", action: #selector(sweepNow), keyEquivalent: "")
        menu.addItem(withTitle: "Open dashboard in browser", action: #selector(openInBrowser), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit Intern Radar", action: #selector(quit), keyEquivalent: "")
        for item in menu.items { item.target = self }
        floatMenuItem.state = defaults.bool(forKey: "float") ? .on : .off
        item.menu = menu
    }

    // MARK: window behaviour

    var isCompact: Bool { window.frame.height <= compactHeight + 1 }

    func applyLevel() {
        // Default: above the desktop icons, below every normal window. Optional: float on top.
        window.level = defaults.bool(forKey: "float")
            ? .floating
            : NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.desktopIconWindow)) + 1)
    }

    func refreshCollapseIcon() {
        let name = isCompact ? "chevron.down" : "chevron.up"
        collapseButton.image = NSImage(systemSymbolName: name, accessibilityDescription: isCompact ? "Expand" : "Collapse")
        collapseButton.toolTip = isCompact ? "Expand" : "Collapse to the counters"
    }

    @objc func toggleCompact() {
        var frame = window.frame
        let top = frame.maxY
        if isCompact {
            let saved = defaults.double(forKey: "expandedHeight")
            frame.size.height = saved > compactHeight + 40 ? saved : 560
        } else {
            defaults.set(Double(frame.height), forKey: "expandedHeight")
            frame.size.height = compactHeight
        }
        frame.origin.y = top - frame.height   // keep the top edge where it is
        window.setFrame(frame, display: true, animate: true)
        refreshCollapseIcon()
    }

    @objc func toggleFloat() {
        defaults.set(!defaults.bool(forKey: "float"), forKey: "float")
        floatMenuItem.state = defaults.bool(forKey: "float") ? .on : .off
        applyLevel()
        window.orderFront(nil)
    }

    @objc func resetPosition() {
        let screen = (window.screen ?? NSScreen.main ?? NSScreen.screens[0]).visibleFrame
        var frame = window.frame
        frame.origin = NSPoint(x: screen.maxX - frame.width - 16, y: screen.maxY - frame.height - 16)
        window.setFrame(frame, display: true, animate: true)
        window.orderFront(nil)
    }

    func windowShouldZoom(_ window: NSWindow, toFrame newFrame: NSRect) -> Bool { false }
    func windowDidResize(_ notification: Notification) { if collapseButton != nil { refreshCollapseIcon() } }

    // MARK: page

    /// Health note for troubleshooting: ~/Library/Logs/InternRadar.status
    func report(_ text: String) {
        let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/InternRadar.status")
        try? "\(Date()) \(text)\n".write(to: url, atomically: true, encoding: .utf8)
    }

    func reload() {
        guard FileManager.default.isReadableFile(atPath: page.path) else {
            web.loadHTMLString("<body style='font:13px -apple-system;padding:44px 18px;color:#888'>"
                + "Intern Radar can't read its dashboard yet.<br><br>If macOS asked for access to the "
                + "Desktop folder, choose Allow, then pick “Sweep now” from the ◎ Radar menu.</body>", baseURL: nil)
            report("cannot read \(page.path) (Desktop access not granted?)")
            return
        }
        loadedVersion = modified(page)
        web.loadFileURL(page, allowingReadAccessTo: page.deletingLastPathComponent())
        refreshAlerts()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        let js = "document.title + ' | tabs=' + document.querySelectorAll('.tab').length + ' | widget=' + document.documentElement.classList.contains('widget')"
        webView.evaluateJavaScript(js) { result, _ in
            self.report("page shown: \(result ?? "?") | visible=\(self.window.isVisible) frame=\(self.window.frame)")
        }
    }

    func modified(_ url: URL) -> Date {
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        return (attrs?[.modificationDate] as? Date) ?? .distantPast
    }

    func tick() {
        if modified(page) > loadedVersion { reload() } else { refreshAlerts() }
        sweepIfDue()
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if action.navigationType == .linkActivated {
            if ["http", "https", "mailto"].contains(url.scheme?.lowercased() ?? "") { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        } else if url.isFileURL || url.absoluteString == "about:blank" {
            decisionHandler(.allow)
        } else {
            decisionHandler(.cancel)   // the page that holds the message bridge never leaves the dashboard
        }
    }

    // MARK: messages from the page

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "link":
            if let text = body["text"] as? String { queueLink(text) }
        case "mail":
            if let key = body["key"] as? String { queueMail(key, body) }
        case "seen":
            if let key = body["key"] as? String, let stamp = body["stamp"] as? String {
                var seen = (readJSON("seen.json") as? [String: Any]) ?? [:]
                seen[key] = stamp
                writeJSON(seen, "seen.json")
                rebuildDashboard()
            }
        case "copy":
            if let text = body["text"] as? String {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(text, forType: .string)
            }
        case "applied":
            if let url = body["url"] as? String, let on = body["on"] as? Bool {
                markApplied(url, on)
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { self.rebuildDashboard() }
            }
        case "stage":
            if let key = body["key"] as? String { setStage(key, body) }
        default:
            break
        }
    }

    func readJSON(_ name: String) -> Any? {
        guard let data = try? Data(contentsOf: dataDir.appendingPathComponent(name)) else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    func writeJSON(_ value: Any, _ name: String) {
        try? FileManager.default.createDirectory(at: dataDir, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted]) {
            try? data.write(to: dataDir.appendingPathComponent(name), options: .atomic)
        }
    }

    func stamp() -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.string(from: Date())
    }

    /// A link (or note) pasted into the widget: stored for Claude to research on its next check.
    func queueLink(_ text: String) {
        var inbox = (readJSON("inbox.json") as? [[String: Any]]) ?? []
        inbox.append(["id": String(UUID().uuidString.prefix(8)), "text": String(text.prefix(2000)),
                      "added": stamp(), "status": "queued"])
        writeJSON(inbox, "inbox.json")
        rebuildDashboard()
    }

    /// "Start cold email" pressed on an opportunity page: queued for Claude to draft (never sent from here).
    func queueMail(_ key: String, _ body: [String: Any]) {
        var inbox = (readJSON("inbox.json") as? [[String: Any]]) ?? []
        let waiting = inbox.contains { ($0["key"] as? String) == key && ($0["status"] as? String) != "done" }
        if !waiting {
            let company = body["company"] as? String ?? ""
            let role = body["role"] as? String ?? ""
            let email = body["email"] as? String ?? ""
            inbox.append(["id": String(UUID().uuidString.prefix(8)), "type": "mail", "key": key,
                          "company": company, "role": role, "email": email,
                          "text": "Cold email: \(company) — \(role) → \(email)",
                          "added": stamp(), "status": "queued"])
            writeJSON(inbox, "inbox.json")
        }
        rebuildDashboard()
    }

    /// Where an application stands (applied / assessment / interview / offer / rejected), with an optional deadline.
    func setStage(_ key: String, _ body: [String: Any]) {
        let stage = body["stage"] as? String ?? "applied"
        let due = body["due"] as? String ?? ""
        var stages = (readJSON("stages.json") as? [String: Any]) ?? [:]
        stages[key] = ["stage": stage, "due": due, "note": String((body["note"] as? String ?? "").prefix(200)),
                       "updated": stamp()]
        writeJSON(stages, "stages.json")
        if let url = body["url"] as? String, !url.isEmpty {
            var applied = (readJSON("applied.json") as? [String: Any]) ?? [:]
            if !(applied[url] is String) { applied[url] = String(stamp().prefix(10)) }
            writeJSON(applied, "applied.json")
        }
        if !due.isEmpty, let date = isoDate(due), ["applied", "assessment", "interview"].contains(stage) {
            addDeadlineReminder(key: key, title: body["title"] as? String ?? "Internship", stage: stage,
                                due: date, dueText: due, link: body["url"] as? String ?? "")
        }
        rebuildDashboard()
    }

    /// A dated step (assessment, interview) goes straight into Reminders so it reaches the phone.
    func addDeadlineReminder(key: String, title: String, stage: String, due: Date, dueText: String, link: String) {
        guard remindersAllowed, let list = events.defaultCalendarForNewReminders() else { return }
        var state = (readJSON("alert_state.json") as? [String: Any]) ?? [:]
        let mark = "rem:\(key):\(stage):\(dueText)"
        if state[mark] != nil { return }
        let reminder = EKReminder(eventStore: events)
        reminder.calendar = list
        reminder.title = "\(stage.capitalized) due: \(title)"
        reminder.notes = link
        if let url = URL(string: link) { reminder.url = url }
        var parts = Calendar.current.dateComponents([.year, .month, .day], from: due)
        parts.hour = 9
        reminder.dueDateComponents = parts
        // Alarm the evening before, and again on the morning itself.
        if let morning = Calendar.current.date(from: parts) {
            reminder.addAlarm(EKAlarm(absoluteDate: morning.addingTimeInterval(-15 * 3600)))
            reminder.addAlarm(EKAlarm(absoluteDate: morning))
        }
        if (try? events.save(reminder, commit: true)) != nil {
            state[mark] = ["created": stamp()]
            writeJSON(state, "alert_state.json")
        }
    }

    func markApplied(_ url: String, _ on: Bool) {
        var applied = (readJSON("applied.json") as? [String: Any]) ?? [:]
        if on { applied[url] = String(stamp().prefix(10)) } else { applied[url] = false }
        writeJSON(applied, "applied.json")
    }

    func python() -> String {
        let conda = "/opt/anaconda3/bin/python3"
        return FileManager.default.isExecutableFile(atPath: conda) ? conda : "/usr/bin/python3"
    }

    func rebuildDashboard() {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: python())
        task.arguments = [root.appendingPathComponent("tools/build_dashboard.py").path]
        task.terminationHandler = { [weak self] _ in DispatchQueue.main.async { self?.reload() } }
        try? task.run()
    }

    // MARK: sweep schedule

    func lastEntry() -> [String: Any]? {
        (readJSON("sweep_log.json") as? [[String: Any]])?.last
    }

    func lastSweep() -> Date {
        guard let text = lastEntry()?["time"] as? String else { return .distantPast }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.date(from: text) ?? .distantPast
    }

    func sweepIfDue() {
        guard online, !sweeping else { return }
        let now = Date()
        guard now.timeIntervalSince(lastSweep()) >= sweepInterval,
              now.timeIntervalSince(lastAttempt) >= retryAfterFailure else { return }
        runSweep()
    }

    func runSweep() {
        guard !sweeping else { return }
        sweeping = true
        lastAttempt = Date()
        sweepMenuItem?.title = "Sweeping…"
        let before = lastEntry()?["time"] as? String
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.arguments = [root.appendingPathComponent("tools/run_sweep.sh").path]
        task.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.sweeping = false
                self.sweepMenuItem?.title = "Sweep now"
                let code = proc.terminationStatus
                // 75 = another sweep (a Claude run) already holds the lock; that is fine.
                self.sweepFailed = code != 0 && code != 75
                if self.sweepFailed { self.report("sweep failed with exit code \(code); see data/sweep.out") }
                _ = before
                self.reload()
            }
        }
        do { try task.run() } catch {
            sweeping = false
            sweepMenuItem?.title = "Sweep now"
        }
    }

    // MARK: alerts — menu-bar count, one notification per qualified match, digest, Reminders

    func setUpNotifications() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let open = UNNotificationAction(identifier: "open", title: "Open posting", options: [.foreground])
        let applied = UNNotificationAction(identifier: "applied", title: "I applied", options: [])
        let skip = UNNotificationAction(identifier: "skip", title: "Not applying", options: [])
        center.setNotificationCategories([
            UNNotificationCategory(identifier: "role", actions: [open, applied, skip], intentIdentifiers: [], options: []),
            UNNotificationCategory(identifier: "note", actions: [], intentIdentifiers: [], options: []),
        ])
        center.requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    func requestReminders() {
        let done: (Bool, Error?) -> Void = { ok, _ in DispatchQueue.main.async { self.remindersAllowed = ok } }
        if #available(macOS 14.0, *) { events.requestFullAccessToReminders(completion: done) }
        else { events.requestAccess(to: .reminder, completion: done) }
    }

    func isoDate(_ text: String) -> Date? {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        for format in ["yyyy-MM-dd'T'HH:mm", "yyyy-MM-dd"] {
            f.dateFormat = format
            if let d = f.date(from: String(text.prefix(format == "yyyy-MM-dd" ? 10 : 16))) { return d }
        }
        return nil
    }

    func refreshAlerts() {
        guard let alerts = readJSON("alerts.json") as? [String: Any] else { return }
        let counts = alerts["counts"] as? [String: Int] ?? [:]
        let apply = (counts["apply"] ?? 0) + (counts["deadlines"] ?? 0), fresh = counts["unreviewed"] ?? 0
        let mail = (counts["drafts"] ?? 0) + (counts["replies"] ?? 0) + (counts["followups"] ?? 0)
        let healthOK = (alerts["health"] as? [String: Any])?["ok"] as? Bool ?? true
        let stale = Date().timeIntervalSince(lastSweep()) > 12 * 3600
        var title = "◎"
        if apply + fresh > 0 { title += " \(apply + fresh)" }
        if mail > 0 { title += " ✉\(mail)" }
        if !healthOK || stale || sweepFailed { title += " ⚠" }
        statusItem?.button?.title = title == "◎" ? "◎ Radar" : title
        var summary = alerts["digest"] as? String ?? "Nothing waiting on you"
        if stale { summary = "⚠ No successful sweep for over 12 hours — " + summary }
        else if sweepFailed { summary = "⚠ Last sweep failed — " + summary }
        summaryMenuItem?.title = summary

        scheduleDigest(alerts["digest"] as? String ?? "", anything: apply + fresh + mail > 0)
        notifyDue(alerts["items"] as? [[String: Any]] ?? [])
    }

    /// One notification at 8:30 the next morning with everything waiting; replaced on every refresh.
    func scheduleDigest(_ text: String, anything: Bool) {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: ["digest"])
        guard anything else { return }
        let content = UNMutableNotificationContent()
        content.title = "Intern Radar — today"
        content.body = text
        content.sound = .default
        content.categoryIdentifier = "note"
        var at = DateComponents()
        at.hour = 8
        at.minute = 30
        center.add(UNNotificationRequest(identifier: "digest", content: content,
                                         trigger: UNCalendarNotificationTrigger(dateMatching: at, repeats: false)))
    }

    /// Strong / good matches: notify when found, again a day later, then put it in Reminders (which
    /// reaches the iPhone through iCloud). New unreviewed roles, drafts and replies notify once.
    func notifyDue(_ items: [[String: Any]]) {
        let now = Date()
        let hour = Calendar.current.component(.hour, from: now)
        guard hour >= 8 && hour < 23 else { return }                    // quiet hours; the digest covers them
        var state = (readJSON("alert_state.json") as? [String: Any]) ?? [:]
        let today = String(stamp().prefix(10))
        var day = state["_day"] as? [String: Any] ?? [:]
        var sentToday = (day["date"] as? String == today) ? (day["count"] as? Int ?? 0) : 0
        var changed = false

        for item in items {
            guard sentToday < maxBannersPerDay,
                  let key = item["key"] as? String, let kind = item["kind"] as? String else { continue }
            var entry = state[key] as? [String: Any] ?? [:]
            if entry["muted"] as? Bool == true { continue }
            let count = entry["count"] as? Int ?? 0
            let last = (entry["last"] as? String).flatMap(isoDate) ?? .distantPast
            let since = (item["since"] as? String).flatMap(isoDate) ?? now
            var prefix: String? = nil
            switch kind {
            case "match":
                if count == 0 { prefix = "" }
                else if count == 1, now.timeIntervalSince(last) >= 24 * 3600 { prefix = "Still open: " }
                else if count == 2, now.timeIntervalSince(last) >= 24 * 3600 {
                    prefix = "Added to Reminders: "
                    addReminder(item)
                }
            case "unreviewed":
                if count == 0, now.timeIntervalSince(since) >= 6 * 3600 { prefix = "New role: " }
            default:
                if count == 0 { prefix = "" }
            }
            guard let lead = prefix else { continue }
            post(item, titlePrefix: lead, role: kind == "match" || kind == "unreviewed")
            if kind == "deadline" { NSSound.beep() }
            entry["count"] = count + 1
            entry["last"] = stamp()
            state[key] = entry
            sentToday += 1
            changed = true
        }
        if changed {
            day = ["date": today, "count": sentToday]
            state["_day"] = day
            writeJSON(state, "alert_state.json")
        }
    }

    func post(_ item: [String: Any], titlePrefix: String, role: Bool) {
        let content = UNMutableNotificationContent()
        content.title = titlePrefix + (item["title"] as? String ?? "Intern Radar")
        content.subtitle = item["subtitle"] as? String ?? ""
        content.body = item["body"] as? String ?? ""
        content.sound = .default
        content.categoryIdentifier = role ? "role" : "note"
        content.userInfo = ["key": item["key"] as? String ?? "", "url": item["url"] as? String ?? ""]
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
    }

    func addReminder(_ item: [String: Any]) {
        guard remindersAllowed, let list = events.defaultCalendarForNewReminders() else { return }
        let reminder = EKReminder(eventStore: events)
        reminder.calendar = list
        reminder.title = "Apply: " + (item["title"] as? String ?? "internship")
        reminder.notes = [item["subtitle"] as? String ?? "", item["url"] as? String ?? ""].joined(separator: "\n")
        if let link = (item["url"] as? String).flatMap(URL.init(string:)) { reminder.url = link }
        var due = Calendar.current.dateComponents([.year, .month, .day], from: Date().addingTimeInterval(24 * 3600))
        due.hour = 9
        reminder.dueDateComponents = due
        if let when = Calendar.current.date(from: due) { reminder.addAlarm(EKAlarm(absoluteDate: when)) }
        try? events.save(reminder, commit: true)
    }

    func mute(_ key: String) {
        var state = (readJSON("alert_state.json") as? [String: Any]) ?? [:]
        var entry = state[key] as? [String: Any] ?? [:]
        entry["muted"] = true
        state[key] = entry
        writeJSON(state, "alert_state.json")
    }

    // Show banners even while the widget is the active app.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let key = info["key"] as? String ?? "", link = info["url"] as? String ?? ""
        DispatchQueue.main.async {
            switch response.actionIdentifier {
            case "applied":
                if !link.isEmpty { self.markApplied(link, true) }
                self.mute(key)
                self.rebuildDashboard()
            case "skip":
                self.mute(key)
            default:   // tapped the banner or chose "Open posting"
                if let url = URL(string: link), ["http", "https"].contains(url.scheme ?? "") { NSWorkspace.shared.open(url) }
                self.showWidget()
            }
            done()
        }
    }

    // MARK: headless sweep (launchd runs `InternRadar --sweep` every 30 minutes)

    /// Runs one sweep if it is due and the Mac is online, without any window. Keeps sweeps going when the
    /// widget has been quit. Returns the process exit code.
    func headlessSweep(force: Bool) -> Int32 {
        let logURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/InternRadar.sweep.status")
        func note(_ text: String) { try? "\(Date()) \(text)\n".write(to: logURL, atomically: true, encoding: .utf8) }
        guard FileManager.default.isReadableFile(atPath: root.appendingPathComponent("tools/run_sweep.sh").path) else {
            note("cannot read the project folder (Desktop access not granted to Intern Radar?)")
            return 2
        }
        let age = Date().timeIntervalSince(lastSweep())
        if !force && age < sweepInterval {
            note("not due: last sweep \(Int(age / 60)) min ago")
            return 0
        }
        let gate = DispatchSemaphore(value: 0)
        var reachable = false
        monitor.pathUpdateHandler = { path in reachable = path.status == .satisfied; gate.signal() }
        monitor.start(queue: DispatchQueue.global(qos: .utility))
        _ = gate.wait(timeout: .now() + 8)
        monitor.cancel()
        guard reachable else { note("offline; will try again"); return 0 }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/zsh")
        task.arguments = [root.appendingPathComponent("tools/run_sweep.sh").path]
        do { try task.run() } catch { note("could not start the sweep: \(error)"); return 3 }
        task.waitUntilExit()
        let code = task.terminationStatus
        note(code == 0 ? "sweep finished" : (code == 75 ? "another sweep was already running" : "sweep failed, exit \(code)"))

        // If the widget is running it sends the alerts; otherwise send them from here.
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
            .filter { $0.processIdentifier != ProcessInfo.processInfo.processIdentifier }
        if others.isEmpty, code == 0, let alerts = readJSON("alerts.json") as? [String: Any] {
            setUpNotifications()
            scheduleDigest(alerts["digest"] as? String ?? "", anything: !((alerts["items"] as? [[String: Any]] ?? []).isEmpty))
            notifyDue(alerts["items"] as? [[String: Any]] ?? [])
            RunLoop.current.run(until: Date().addingTimeInterval(4))
        }
        return (code == 0 || code == 75) ? 0 : code
    }

    // MARK: actions

    @objc func woke() { DispatchQueue.main.asyncAfter(deadline: .now() + 20) { self.tick() } }
    @objc func sweepNow() { lastAttempt = .distantPast; runSweep() }
    @objc func toggleWindow() { window.isVisible ? window.orderOut(nil) : window.orderFront(nil) }
    @objc func showWidget() { window.orderFrontRegardless() }
    @objc func openInBrowser() { NSWorkspace.shared.open(page) }
    @objc func quit() { NSApp.terminate(nil) }
}

if CommandLine.arguments.contains("--sweep") {
    exit(AppDelegate().headlessSweep(force: CommandLine.arguments.contains("--force")))
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
