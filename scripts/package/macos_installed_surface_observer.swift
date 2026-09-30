import AppKit
import ApplicationServices
import CoreGraphics
import Darwin
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let productName = "HarnessKit"
let productBundleIdentifier = "io.github.pureliture.harnesskit"
let spotlightBundleIdentifier = "com.apple.Spotlight"
let dockBundleIdentifier = "com.apple.dock"
let legacyLaunchpadPath = "/System/Applications/Launchpad.app"

func emit(_ value: Any) -> Never {
    let body = try! JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(body)
    FileHandle.standardOutput.write(Data([0x0a]))
    exit(0)
}

func failJSON(
    _ code: String,
    _ operation: String,
    evidence: [String: Any] = [:]
) -> Never {
    var error: [String: Any] = ["code": code, "operation": operation]
    if !evidence.isEmpty {
        error["evidence"] = evidence
    }
    let payload: [String: Any] = ["error": error]
    let body = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    FileHandle.standardError.write(body)
    FileHandle.standardError.write(Data([0x0a]))
    exit(2)
}

func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name, &value) == .success else { return nil }
    return value
}

func text(_ element: AXUIElement, _ name: CFString) -> String {
    attribute(element, name) as? String ?? ""
}

func normalized(_ value: String) -> String {
    value.precomposedStringWithCanonicalMapping
}

func elementURLPath(_ element: AXUIElement) -> String {
    let raw = attribute(element, kAXURLAttribute as CFString)
    if let url = raw as? URL {
        return url.standardizedFileURL.path
    }
    if let url = raw as? NSURL {
        return (url as URL).standardizedFileURL.path
    }
    if let value = raw as? String, let url = URL(string: value), url.isFileURL {
        return url.standardizedFileURL.path
    }
    return ""
}

func flag(_ element: AXUIElement, _ name: CFString) -> Bool {
    (attribute(element, name) as? NSNumber)?.boolValue ?? false
}

func children(_ element: AXUIElement) -> [AXUIElement] {
    attribute(element, kAXChildrenAttribute as CFString) as? [AXUIElement] ?? []
}

func actionNames(_ element: AXUIElement) -> [String] {
    var names: CFArray?
    guard AXUIElementCopyActionNames(element, &names) == .success else { return [] }
    return names as? [String] ?? []
}

func descendants(_ root: AXUIElement, limit: Int = 5000) -> [AXUIElement] {
    var output: [AXUIElement] = []
    var queue = [root]
    var seen = Set<CFHashCode>()
    while !queue.isEmpty && output.count < limit {
        let element = queue.removeFirst()
        guard seen.insert(CFHash(element)).inserted else { continue }
        output.append(element)
        queue.append(contentsOf: children(element))
    }
    return output
}

func axFrame(_ element: AXUIElement) -> CGRect? {
    guard let rawPosition = attribute(element, kAXPositionAttribute as CFString),
          let rawSize = attribute(element, kAXSizeAttribute as CFString),
          CFGetTypeID(rawPosition) == AXValueGetTypeID(),
          CFGetTypeID(rawSize) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero
    var size = CGSize.zero
    guard AXValueGetValue(rawPosition as! AXValue, .cgPoint, &point),
          AXValueGetValue(rawSize as! AXValue, .cgSize, &size) else { return nil }
    return CGRect(origin: point, size: size)
}

func frameRecord(_ frame: CGRect) -> [String: Double] {
    return [
        "x": frame.origin.x,
        "y": frame.origin.y,
        "width": frame.width,
        "height": frame.height,
    ]
}

func cgFrame(_ value: Any?) -> CGRect? {
    guard let dictionary = value as? NSDictionary else { return nil }
    return CGRect(dictionaryRepresentation: dictionary as CFDictionary)
}

func framesMatch(_ left: CGRect, _ right: CGRect, tolerance: CGFloat = 3) -> Bool {
    abs(left.minX - right.minX) <= tolerance &&
        abs(left.minY - right.minY) <= tolerance &&
        abs(left.width - right.width) <= tolerance &&
        abs(left.height - right.height) <= tolerance
}

func postKey(_ code: CGKeyCode, flags: CGEventFlags = []) {
    let source = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        guard let event = CGEvent(
            keyboardEventSource: source,
            virtualKey: code,
            keyDown: down
        ) else { continue }
        event.flags = flags
        event.post(tap: .cghidEventTap)
        usleep(40_000)
    }
}

func postText(_ value: String) {
    let utf16 = Array(value.utf16)
    let source = CGEventSource(stateID: .hidSystemState)
    for down in [true, false] {
        guard let event = CGEvent(
            keyboardEventSource: source,
            virtualKey: 0,
            keyDown: down
        ) else { continue }
        event.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: utf16)
        event.post(tap: .cghidEventTap)
    }
}

func click(_ element: AXUIElement) -> Bool {
    guard let frame = axFrame(element), frame.width > 0, frame.height > 0,
          let source = CGEventSource(stateID: .hidSystemState) else { return false }
    let point = CGPoint(x: frame.midX, y: frame.midY)
    let kinds: [CGEventType] = [.mouseMoved, .leftMouseDown, .leftMouseUp]
    for kind in kinds {
        guard let event = CGEvent(
            mouseEventSource: source,
            mouseType: kind,
            mouseCursorPosition: point,
            mouseButton: .left
        ) else { return false }
        event.post(tap: .cghidEventTap)
        usleep(60_000)
    }
    return true
}

func runningApplication(_ bundleIdentifier: String) -> NSRunningApplication? {
    NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
        .filter { !$0.isTerminated }
        .sorted { $0.processIdentifier < $1.processIdentifier }
        .first
}

func windowInventory() -> [[String: Any]] {
    CGWindowListCopyWindowInfo(
        [.optionOnScreenOnly, .excludeDesktopElements],
        kCGNullWindowID
    ) as? [[String: Any]] ?? []
}

func cgWindow(
    pid: pid_t,
    title: String? = nil,
    layer: Int? = nil,
    frame: CGRect? = nil
) -> [String: Any]? {
    windowInventory().first { record in
        guard (record[kCGWindowOwnerPID as String] as? Int) == Int(pid) else { return false }
        if let title,
           (record[kCGWindowName as String] as? String ?? "") != title { return false }
        if let layer,
           (record[kCGWindowLayer as String] as? Int) != layer { return false }
        if let frame {
            guard let candidate = cgFrame(record[kCGWindowBounds as String]),
                  framesMatch(candidate, frame) else { return false }
        }
        return true
    }
}

func pngData(_ image: CGImage) -> Data? {
    let buffer = NSMutableData()
    guard let encoder = CGImageDestinationCreateWithData(
        buffer,
        UTType.png.identifier as CFString,
        1,
        nil
    ) else { return nil }
    CGImageDestinationAddImage(encoder, image, nil)
    guard CGImageDestinationFinalize(encoder) else { return nil }
    return buffer as Data
}

struct ExactWindow {
    let bundleIdentifier: String
    let ownerPID: pid_t
    let windowID: CGWindowID
    let title: String
    let layer: Int
}

@available(macOS 14.0, *)
func captureWindow(
    _ expected: ExactWindow,
    destination: URL,
    includeCursor: Bool,
    extra: [String: Any] = [:]
) async -> [String: Any]? {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(
            true,
            onScreenWindowsOnly: true
        )
        let identityMatches = content.windows.filter {
            $0.owningApplication?.processID == expected.ownerPID &&
                $0.owningApplication?.bundleIdentifier == expected.bundleIdentifier &&
                ($0.title ?? "") == expected.title &&
                $0.windowLayer == expected.layer &&
                $0.isOnScreen
        }
        let exactWindow = identityMatches.first {
            $0.windowID == expected.windowID
        }
        let uniqueReplacement = identityMatches.count == 1
            ? identityMatches[0]
            : nil
        guard let window = exactWindow ?? uniqueReplacement else { return nil }
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let configuration = SCStreamConfiguration()
        let scale = CGFloat(filter.pointPixelScale)
        configuration.width = max(1, Int((filter.contentRect.width * scale).rounded(.up)))
        configuration.height = max(1, Int((filter.contentRect.height * scale).rounded(.up)))
        configuration.showsCursor = includeCursor
        configuration.shouldBeOpaque = false
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        guard let body = pngData(image) else { return nil }
        try body.write(to: destination, options: .atomic)
        var result = extra
        result.merge([
            "accepted": true,
            "capture_mode": "screen_capture_kit_desktop_independent_window",
            "cursor_included": includeCursor,
            "owner_pid": Int(window.owningApplication?.processID ?? expected.ownerPID),
            "window_id": Int(window.windowID),
            "window_layer": window.windowLayer,
            "window_title": window.title ?? expected.title,
            "pixel_width": image.width,
            "pixel_height": image.height,
            "point_pixel_scale": filter.pointPixelScale,
            "screenshot": destination.path,
        ]) { _, right in right }
        return result
    } catch {
        return nil
    }
}

@available(macOS 14.0, *)
func captureMainDisplay(
    destination: URL,
    includeCursor: Bool,
    extra: [String: Any] = [:]
) async -> [String: Any]? {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
        let displayID = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == displayID }) else {
            return nil
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        let scale = CGFloat(NSScreen.main?.backingScaleFactor ?? 2)
        configuration.width = max(1, Int(CGDisplayBounds(displayID).width * scale))
        configuration.height = max(1, Int(CGDisplayBounds(displayID).height * scale))
        configuration.showsCursor = includeCursor
        configuration.shouldBeOpaque = true
        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        guard let body = pngData(image) else { return nil }
        try body.write(to: destination, options: .atomic)
        var result = extra
        result.merge([
            "accepted": true,
            "capture_mode": "screen_capture_kit_main_display",
            "cursor_included": includeCursor,
            "display_id": Int(displayID),
            "pixel_width": image.width,
            "pixel_height": image.height,
            "screenshot": destination.path,
        ]) { _, right in right }
        return result
    } catch {
        return nil
    }
}

struct FinderLayout {
    let finderPID: pid_t
    let window: AXUIElement
    let windowIdentity: ExactWindow
    let source: AXUIElement
    let applications: AXUIElement
    let sourceFrame: CGRect
    let applicationsFrame: CGRect
}

func finderElement(_ nodes: [AXUIElement], names: Set<String>) -> AXUIElement? {
    nodes.first { element in
        let candidates = [
            text(element, kAXTitleAttribute as CFString),
            text(element, kAXIdentifierAttribute as CFString),
            text(element, kAXDescriptionAttribute as CFString),
            text(element, kAXValueAttribute as CFString),
        ]
        return candidates.contains(where: { names.contains($0) }) &&
            axFrame(element).map { $0.width > 20 && $0.height > 20 } == true
    }
}

func elementPID(_ element: AXUIElement) -> pid_t? {
    var pid = pid_t()
    guard AXUIElementGetPid(element, &pid) == .success else { return nil }
    return pid
}

func parentElement(_ element: AXUIElement) -> AXUIElement? {
    guard let raw = attribute(element, kAXParentAttribute as CFString),
          CFGetTypeID(raw) == AXUIElementGetTypeID() else { return nil }
    return (raw as! AXUIElement)
}

func exactFinderItem(
    _ nodes: [AXUIElement],
    path: String,
    finderPID: pid_t
) -> AXUIElement? {
    let expected = URL(fileURLWithPath: path).standardizedFileURL.path
    let matches = nodes.filter { element in
        elementPID(element) == finderPID &&
            elementURLPath(element) == expected &&
            axFrame(element).map { $0.width > 20 && $0.height > 20 } == true
    }
    let outermost = matches.filter { element in
        guard let parent = parentElement(element), elementPID(parent) == finderPID else {
            return true
        }
        return elementURLPath(parent) != expected
    }
    return outermost.count == 1 ? outermost[0] : nil
}

func exactFinderItem(at point: CGPoint, path: String, finderPID: pid_t) -> Bool {
    let system = AXUIElementCreateSystemWide()
    var element: AXUIElement?
    guard AXUIElementCopyElementAtPosition(
        system,
        Float(point.x),
        Float(point.y),
        &element
    ) == .success else { return false }
    let expected = URL(fileURLWithPath: path).standardizedFileURL.path
    for _ in 0..<12 {
        guard let current = element else { return false }
        if elementPID(current) == finderPID,
           elementURLPath(current) == expected,
           axFrame(current).map({ $0.width > 20 && $0.height > 20 }) == true {
            return true
        }
        element = parentElement(current)
    }
    return false
}

func bundleInventory(_ path: String) -> [String]? {
    let root = URL(fileURLWithPath: path, isDirectory: true).standardizedFileURL.path
    guard let enumerator = FileManager.default.enumerator(atPath: root) else {
        return nil
    }
    var records: [String] = []
    for case let relative as String in enumerator {
        let entry = (root as NSString).appendingPathComponent(relative)
        var metadata = stat()
        guard lstat(entry, &metadata) == 0 else { return nil }
        let kind = metadata.st_mode & mode_t(S_IFMT)
        if kind == mode_t(S_IFLNK) {
            guard let target = try? FileManager.default.destinationOfSymbolicLink(
                atPath: entry
            ) else { return nil }
            records.append("\(relative)|link|\(target)")
        } else if kind == mode_t(S_IFDIR) {
            records.append("\(relative)|directory")
        } else if kind == mode_t(S_IFREG) {
            records.append("\(relative)|file|\(metadata.st_size)")
        } else {
            records.append("\(relative)|other")
        }
    }
    return records.sorted()
}

func finderLayout(sourcePath: String, applicationsPath: String) -> FinderLayout? {
    guard let finder = runningApplication("com.apple.finder") else { return nil }
    let root = AXUIElementCreateApplication(finder.processIdentifier)
    let windows = attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
    for window in windows {
        guard text(window, kAXRoleAttribute as CFString) == "AXWindow",
              let windowFrame = axFrame(window) else { continue }
        let nodes = descendants(window, limit: 1500)
        guard let source = exactFinderItem(
                nodes,
                path: sourcePath,
                finderPID: finder.processIdentifier
              ),
              let applications = exactFinderItem(
                nodes,
                path: applicationsPath,
                finderPID: finder.processIdentifier
              ),
              let sourceFrame = axFrame(source),
              let applicationsFrame = axFrame(applications),
              let record = cgWindow(
                pid: finder.processIdentifier,
                layer: 0,
                frame: windowFrame
              ),
              let id = record[kCGWindowNumber as String] as? Int else { continue }
        let title = record[kCGWindowName as String] as? String ?? text(
            window,
            kAXTitleAttribute as CFString
        )
        return FinderLayout(
            finderPID: finder.processIdentifier,
            window: window,
            windowIdentity: ExactWindow(
                bundleIdentifier: "com.apple.finder",
                ownerPID: finder.processIdentifier,
                windowID: CGWindowID(id),
                title: title,
                layer: 0
            ),
            source: source,
            applications: applications,
            sourceFrame: sourceFrame,
            applicationsFrame: applicationsFrame
        )
    }
    return nil
}

func temporaryScreenshot(_ stem: String) -> URL {
    FileManager.default.temporaryDirectory
        .appendingPathComponent("\(stem)-\(UUID().uuidString).png")
}

func finderLayoutPayload(_ layout: FinderLayout, mountpoint: String) -> [String: Any] {
    [
        "accepted": true,
        "app": URL(fileURLWithPath: mountpoint)
            .appendingPathComponent("HarnessKit.app").path,
        "applications_alias": URL(fileURLWithPath: mountpoint)
            .appendingPathComponent("Applications").path,
        "mountpoint": mountpoint,
        "app_center": [180, 190],
        "applications_center": [480, 190],
        "app_screen_frame": frameRecord(layout.sourceFrame),
        "applications_screen_frame": frameRecord(layout.applicationsFrame),
        "instruction_visible": true,
        "owner_pid": Int(layout.windowIdentity.ownerPID),
        "window_id": Int(layout.windowIdentity.windowID),
        "window_layer": layout.windowIdentity.layer,
        "window_title": layout.windowIdentity.title,
    ]
}

func finderRevealWindow() -> (ExactWindow, CGRect)? {
    guard let finder = runningApplication("com.apple.finder") else { return nil }
    let root = AXUIElementCreateApplication(finder.processIdentifier)
    let windows = attribute(root, kAXWindowsAttribute as CFString) as? [AXUIElement] ?? []
    for window in windows {
        guard let windowFrame = axFrame(window) else { continue }
        let nodes = descendants(window, limit: 2500)
        guard let item = finderElement(nodes, names: [productName]),
              let itemFrame = axFrame(item),
              let record = cgWindow(
                pid: finder.processIdentifier,
                layer: 0,
                frame: windowFrame
              ),
              let id = record[kCGWindowNumber as String] as? Int else { continue }
        let title = record[kCGWindowName as String] as? String ?? text(
            window,
            kAXTitleAttribute as CFString
        )
        return (
            ExactWindow(
                bundleIdentifier: "com.apple.finder",
                ownerPID: finder.processIdentifier,
                windowID: CGWindowID(id),
                title: title,
                layer: 0
            ),
            itemFrame
        )
    }
    return nil
}

func spotlightCGWindow() -> ExactWindow? {
    guard let app = runningApplication(spotlightBundleIdentifier) else { return nil }
    guard let record = windowInventory().first(where: {
        ($0[kCGWindowOwnerPID as String] as? Int) == Int(app.processIdentifier) &&
            ($0[kCGWindowOwnerName as String] as? String) == "Spotlight" &&
            ($0[kCGWindowName as String] as? String) == "Spotlight" &&
            ($0[kCGWindowLayer as String] as? Int) == 23
    }), let id = record[kCGWindowNumber as String] as? Int else { return nil }
    return ExactWindow(
        bundleIdentifier: spotlightBundleIdentifier,
        ownerPID: app.processIdentifier,
        windowID: CGWindowID(id),
        title: "Spotlight",
        layer: 23
    )
}

func spotlightRoot() -> AXUIElement? {
    runningApplication(spotlightBundleIdentifier).map {
        AXUIElementCreateApplication($0.processIdentifier)
    }
}

func legacyLaunchpadRoot() -> AXUIElement? {
    runningApplication(dockBundleIdentifier).map {
        AXUIElementCreateApplication($0.processIdentifier)
    }
}

func legacyLaunchpadSearchField(_ root: AXUIElement) -> AXUIElement? {
    descendants(root, limit: 5000).first { element in
        let role = text(element, kAXRoleAttribute as CFString)
        let subrole = text(element, kAXSubroleAttribute as CFString)
        guard role == "AXTextField" || role == "AXSearchField" || subrole == "AXSearchField"
        else { return false }
        return axFrame(element).map { $0.width > 40 && $0.height > 16 } == true
    }
}

func launchpadCGWindow() -> ExactWindow? {
    guard let dock = runningApplication(dockBundleIdentifier) else { return nil }
    let display = CGDisplayBounds(CGMainDisplayID())
    let matches = windowInventory().compactMap { record -> (ExactWindow, CGRect)? in
        guard (record[kCGWindowOwnerPID as String] as? Int) == Int(dock.processIdentifier),
              let frame = cgFrame(record[kCGWindowBounds as String]),
              framesMatch(frame, display, tolerance: 24),
              let identifier = record[kCGWindowNumber as String] as? Int,
              let layer = record[kCGWindowLayer as String] as? Int else { return nil }
        let title = record[kCGWindowName as String] as? String ?? ""
        return (
            ExactWindow(
                bundleIdentifier: dockBundleIdentifier,
                ownerPID: dock.processIdentifier,
                windowID: CGWindowID(identifier),
                title: title,
                layer: layer
            ),
            frame
        )
    }
    guard matches.count == 1 else { return nil }
    return matches[0].0
}

func closeLegacyLaunchpadSurface() {
    guard launchpadCGWindow() != nil else { return }
    postKey(53)
    for _ in 0..<30 {
        if launchpadCGWindow() == nil { return }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

func openLegacyLaunchpadSurface() -> (AXUIElement, ExactWindow)? {
    let hostMajor = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
    guard (13...15).contains(hostMajor) else {
        return nil
    }
    if let root = legacyLaunchpadRoot(),
       let window = launchpadCGWindow(),
       legacyLaunchpadSearchField(root) != nil {
        return (root, window)
    }
    closeSpotlightSurface()
    closeLegacyLaunchpadSurface()
    let launchpadURL = URL(fileURLWithPath: legacyLaunchpadPath)
    guard FileManager.default.fileExists(atPath: launchpadURL.path) else { return nil }
    var completionObserved = false
    var opened = false
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    NSWorkspace.shared.openApplication(
        at: launchpadURL,
        configuration: configuration
    ) { _, error in
        opened = error == nil
        completionObserved = true
    }
    let completionDeadline = Date(timeIntervalSinceNow: 5)
    while !completionObserved && Date() < completionDeadline {
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
    }
    guard completionObserved, opened else { return nil }
    for _ in 0..<80 {
        if let root = legacyLaunchpadRoot(),
           let window = launchpadCGWindow(),
           legacyLaunchpadSearchField(root) != nil {
            return (root, window)
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return nil
}

func sceneIsCatalog(_ root: AXUIElement) -> Bool {
    descendants(root, limit: 3500).contains {
        let identifier = text($0, kAXIdentifierAttribute as CFString)
        return identifier == "QueryFilterBar" || (
            text($0, kAXRoleAttribute as CFString) == "AXCell" &&
                identifier.hasPrefix("Identifier:GridCell")
        )
    }
}

func closeSpotlightSurface() {
    guard spotlightCGWindow() != nil else { return }
    postKey(53)
    for _ in 0..<30 {
        if spotlightCGWindow() == nil { return }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

func openCatalogSurface() -> (AXUIElement, ExactWindow)? {
    if let root = spotlightRoot(), let window = spotlightCGWindow(), sceneIsCatalog(root) {
        return (root, window)
    }
    closeSpotlightSurface()
    guard let dock = runningApplication("com.apple.dock") else { return nil }
    let dockRoot = AXUIElementCreateApplication(dock.processIdentifier)
    let items = descendants(dockRoot, limit: 500).filter {
        text($0, kAXRoleAttribute as CFString) == "AXDockItem" &&
            text($0, kAXSubroleAttribute as CFString) == "AXApplicationDockItem" &&
            elementURLPath($0) == "/System/Applications/Apps.app"
    }
    guard items.count == 1, click(items[0]) else {
        return nil
    }
    for _ in 0..<50 {
        if let root = spotlightRoot(), let window = spotlightCGWindow(), sceneIsCatalog(root) {
            return (root, window)
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return nil
}

func spotlightMenuExtra(_ root: AXUIElement) -> AXUIElement? {
    let matches = descendants(root, limit: 1500).filter {
        text($0, kAXRoleAttribute as CFString) == "AXMenuBarItem" &&
            text($0, kAXSubroleAttribute as CFString) == "AXMenuExtra" &&
            text($0, kAXTitleAttribute as CFString) == "Spotlight"
    }
    return matches.count == 1 ? matches[0] : nil
}

func spotlightSearchField(_ root: AXUIElement) -> AXUIElement? {
    descendants(root, limit: 3500).first {
        text($0, kAXRoleAttribute as CFString) == "AXTextField" &&
            text($0, kAXIdentifierAttribute as CFString) == "SpotlightSearchField"
    }
}

struct SpotlightOnboardingStrings {
    let continueLabel: String
    let markers: Set<String>
}

func spotlightOnboardingStrings() -> SpotlightOnboardingStrings? {
    let tableURL = URL(
        fileURLWithPath: "/System/Library/CoreServices/Spotlight.app/Contents/Resources/FirstTimeExperience.loctable"
    )
    guard let data = try? Data(contentsOf: tableURL),
          let plist = try? PropertyListSerialization.propertyList(
              from: data,
              options: [],
              format: nil
          ),
          let table = plist as? [String: Any] else { return nil }
    let localizations = table.keys.filter { $0 != "LocProvenance" }
    let preferences = UserDefaults.standard.stringArray(forKey: "AppleLanguages")
        ?? Locale.preferredLanguages
    let selected = Bundle.preferredLocalizations(
        from: localizations,
        forPreferences: preferences
    ).first ?? "en"
    guard let values = table[selected] as? [String: Any],
          let continueLabel = values["FTE_CONTINUE_BUTTON_LABEL"] as? String else {
        return nil
    }
    let markers = Set(values.compactMap { key, raw -> String? in
        guard key != "FTE_CONTINUE_BUTTON_LABEL",
              key.hasPrefix("FTE_"),
              key.contains("TITLE") || key.contains("DESCRIPTION") || key.contains("SUBTITLE"),
              let value = raw as? String,
              !value.isEmpty else { return nil }
        return normalized(value)
    })
    guard !markers.isEmpty else { return nil }
    return SpotlightOnboardingStrings(
        continueLabel: normalized(continueLabel),
        markers: markers
    )
}

func dismissSpotlightOnboarding(_ root: AXUIElement) -> Bool {
    if spotlightSearchField(root) != nil { return true }
    guard let strings = spotlightOnboardingStrings() else { return false }
    let nodes = descendants(root, limit: 3500)
    let visibleTexts = Set(nodes.flatMap { element in
        [
            text(element, kAXTitleAttribute as CFString),
            text(element, kAXDescriptionAttribute as CFString),
            text(element, kAXValueAttribute as CFString),
        ].filter { !$0.isEmpty }.map(normalized)
    })
    guard !visibleTexts.isDisjoint(with: strings.markers) else { return false }
    let buttons = nodes.filter { element in
        guard text(element, kAXRoleAttribute as CFString) == "AXButton" else {
            return false
        }
        return [
            text(element, kAXTitleAttribute as CFString),
            text(element, kAXDescriptionAttribute as CFString),
            text(element, kAXValueAttribute as CFString),
        ].map(normalized).contains(strings.continueLabel)
    }
    guard buttons.count == 1 else { return false }
    if AXUIElementPerformAction(buttons[0], kAXPressAction as CFString) != .success,
       !click(buttons[0]) {
        return false
    }
    for _ in 0..<50 {
        if spotlightSearchField(root) != nil { return true }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return false
}

func openStandardSpotlightSurface() -> (AXUIElement, ExactWindow)? {
    if let root = spotlightRoot(), let window = spotlightCGWindow(), !sceneIsCatalog(root) {
        if spotlightSearchField(root) != nil || dismissSpotlightOnboarding(root) {
            return (root, window)
        }
    }
    closeSpotlightSurface()
    guard let root = spotlightRoot(), let menu = spotlightMenuExtra(root), click(menu) else {
        return nil
    }
    for _ in 0..<50 {
        if let root = spotlightRoot(), let window = spotlightCGWindow(), !sceneIsCatalog(root) {
            if spotlightSearchField(root) != nil || dismissSpotlightOnboarding(root) {
                return (root, window)
            }
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return nil
}

func setExactSearchQuery(_ field: AXUIElement, query: String) -> Bool {
    guard AXUIElementSetAttributeValue(
        field,
        kAXFocusedAttribute as CFString,
        kCFBooleanTrue
    ) == .success else { return false }
    guard AXUIElementSetAttributeValue(
        field,
        kAXValueAttribute as CFString,
        query as CFString
    ) == .success else { return false }
    for _ in 0..<10 {
        if text(field, kAXValueAttribute as CFString) == query { return true }
        Thread.sleep(forTimeInterval: 0.05)
    }
    return false
}

struct SearchResult {
    let root: AXUIElement
    let window: ExactWindow
    let field: AXUIElement
    let cell: AXUIElement
    let cellFrame: CGRect
    let identifier: String
    let iconFrame: CGRect
}

func legacyLaunchpadResult(
    query: String,
    bundleIdentifier: String,
    destination: String,
    resultPollCount: Int = 40
) -> SearchResult? {
    let destinationURL = URL(fileURLWithPath: destination).standardizedFileURL
    guard Bundle(url: destinationURL)?.bundleIdentifier == bundleIdentifier,
          let (root, window) = openLegacyLaunchpadSurface(),
          let field = legacyLaunchpadSearchField(root),
          setExactSearchQuery(field, query: query) else { return nil }
    for _ in 0..<resultPollCount {
        let nodes = descendants(root, limit: 5000)
        let exactItems = nodes.filter { candidate in
            let role = text(candidate, kAXRoleAttribute as CFString)
            guard role == "AXButton" || actionNames(candidate).contains(
                kAXPressAction as String
            ), let frame = axFrame(candidate), frame.width >= 32, frame.height >= 32 else {
                return false
            }
            let names = [
                text(candidate, kAXTitleAttribute as CFString),
                text(candidate, kAXDescriptionAttribute as CFString),
                text(candidate, kAXValueAttribute as CFString),
            ].map(normalized)
            return names.contains(normalized(query))
        }
        if exactItems.count == 1, let item = exactItems.first,
           let itemFrame = axFrame(item),
           text(field, kAXValueAttribute as CFString) == query {
            let role = text(item, kAXRoleAttribute as CFString)
            let rawIdentifier = text(item, kAXIdentifierAttribute as CFString)
            return SearchResult(
                root: root,
                window: window,
                field: field,
                cell: item,
                cellFrame: itemFrame,
                identifier: rawIdentifier.isEmpty
                    ? "Launchpad:\(role):\(normalized(query))"
                    : rawIdentifier,
                iconFrame: itemFrame
            )
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return nil
}

func searchResult(
    surface: String,
    query: String,
    bundleIdentifier: String,
    destination: String,
    resultPollCount: Int = 40
) -> SearchResult? {
    if surface == "spotlight_name" {
        closeSpotlightSurface()
    }
    let scene = surface == "spotlight_apps"
        ? openCatalogSurface()
        : openStandardSpotlightSurface()
    guard let (root, window) = scene else { return nil }
    guard let field = spotlightSearchField(root) else { return nil }
    guard setExactSearchQuery(field, query: query) else { return nil }
    for _ in 0..<resultPollCount {
        let nodes = descendants(root, limit: 5000)
        let expectedPrefix = surface == "spotlight_apps"
            ? "Identifier:GridCell"
            : "Identifier:ResultCell"
        let exactCells = nodes.filter { candidate in
            guard text(candidate, kAXRoleAttribute as CFString) == "AXCell",
                  text(candidate, kAXIdentifierAttribute as CFString).hasPrefix(expectedPrefix) else {
                return false
            }
            if surface != "spotlight_apps" && !text(
                candidate,
                kAXIdentifierAttribute as CFString
            ).contains("Bundle:com.apple.applications") {
                return false
            }
            return descendants(candidate, limit: 100).contains {
                text($0, kAXRoleAttribute as CFString) == "AXStaticText" &&
                    normalized(text($0, kAXValueAttribute as CFString)) == normalized(query)
            }
        }
        if exactCells.count == 1, let cell = exactCells.first,
           let cellFrame = axFrame(cell),
           let image = descendants(cell, limit: 100).first(where: {
               text($0, kAXRoleAttribute as CFString) == "AXImage"
           }), let imageFrame = axFrame(image),
           text(field, kAXValueAttribute as CFString) == query {
            return SearchResult(
                root: root,
                window: window,
                field: field,
                cell: cell,
                cellFrame: cellFrame,
                identifier: text(cell, kAXIdentifierAttribute as CFString),
                iconFrame: imageFrame
            )
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return nil
}

func activateSearchResult(_ result: SearchResult, sourceName: String) -> Bool {
    if sourceName == "spotlight_name" {
        postKey(36)
        return true
    }
    return click(result.cell)
}

func searchPayload(
    _ result: SearchResult,
    surface: String,
    query: String,
    bundleIdentifier: String,
    destination: String
) -> [String: Any] {
    return [
        "ready": true,
        "surface": surface,
        "bundle_identifier": bundleIdentifier,
        "name": query,
        "path": destination,
        "icon_visible": result.iconFrame.width > 0 && result.iconFrame.height > 0,
        "cell_identifier": result.identifier,
        "cell_frame": frameRecord(result.cellFrame),
        "icon_frame": frameRecord(result.iconFrame),
        "owner_pid": Int(result.window.ownerPID),
        "window_id": Int(result.window.windowID),
        "window_layer": result.window.layer,
        "window_title": result.window.title,
        "query_readback": text(result.field, kAXValueAttribute as CFString),
    ]
}

func mainWindowCount(_ pid: pid_t) -> (Int, ExactWindow?) {
    let records = windowInventory().filter {
        ($0[kCGWindowOwnerPID as String] as? Int) == Int(pid) &&
            ($0[kCGWindowLayer as String] as? Int) == 0 &&
            ($0[kCGWindowIsOnscreen as String] as? Bool) == true
    }
    guard let record = records.first,
          let id = record[kCGWindowNumber as String] as? Int else {
        return (records.count, nil)
    }
    return (
        records.count,
        ExactWindow(
            bundleIdentifier: productBundleIdentifier,
            ownerPID: pid,
            windowID: CGWindowID(id),
            title: record[kCGWindowName as String] as? String ?? productName,
            layer: 0
        )
    )
}

func stopExactApplication(
    bundleIdentifier: String,
    expectedExecutable: String
) -> Bool {
    let running = NSRunningApplication.runningApplications(
        withBundleIdentifier: bundleIdentifier
    ).filter { !$0.isTerminated }
    guard running.allSatisfy({
        $0.executableURL?.standardizedFileURL.path == expectedExecutable
    }) else { return false }
    for application in running {
        guard application.terminate() else { return false }
    }
    for _ in 0..<50 {
        if NSRunningApplication.runningApplications(
            withBundleIdentifier: bundleIdentifier
        ).filter({ !$0.isTerminated }).isEmpty {
            return true
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    return false
}

func interactiveSessionAvailable() -> Bool {
    guard let session = CGSessionCopyCurrentDictionary() as? [String: Any],
          let onConsole = session[kCGSessionOnConsoleKey as String] as? NSNumber,
          let sessionUserID = session[kCGSessionUserIDKey as String] as? NSNumber,
          NSWorkspace.shared.frontmostApplication != nil else {
        return false
    }
    return onConsole.boolValue && sessionUserID.uint32Value == geteuid()
}

func interactiveSessionLocked() -> Bool {
    NSWorkspace.shared.frontmostApplication?.bundleIdentifier == "com.apple.loginwindow"
}

func dockItem(named name: String) -> (AXUIElement, CGRect)? {
    guard let dock = runningApplication("com.apple.dock") else { return nil }
    let root = AXUIElementCreateApplication(dock.processIdentifier)
    guard let item = descendants(root, limit: 500).first(where: {
        text($0, kAXRoleAttribute as CFString) == "AXDockItem" &&
            text($0, kAXTitleAttribute as CFString) == name
    }), let frame = axFrame(item) else { return nil }
    return (item, frame)
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else { failJSON("observer_arguments_invalid", "dispatch") }
let command = arguments[1]

if command == "preflight" {
    let frontmost = NSWorkspace.shared.frontmostApplication
    let session = CGSessionCopyCurrentDictionary() as? [String: Any]
    let onConsole = (session?[kCGSessionOnConsoleKey as String] as? NSNumber)?.boolValue == true
    let sessionUserID = (session?[kCGSessionUserIDKey as String] as? NSNumber)?.uint32Value
    emit([
        "accessibility": AXIsProcessTrusted(),
        "screen_capture": CGPreflightScreenCaptureAccess(),
        "session_dictionary_available": session != nil,
        "session_on_console": onConsole,
        "session_user_matches": sessionUserID == geteuid(),
        "frontmost_application_available": frontmost != nil,
        "session_unlocked": frontmost?.bundleIdentifier != "com.apple.loginwindow",
    ])
}

guard AXIsProcessTrusted() else { failJSON("accessibility_api_denied", command) }

if command == "stop-owned-application" {
    guard arguments.count == 4,
          arguments[2] == productBundleIdentifier else {
        failJSON("owned_application_arguments_invalid", command)
    }
    let destination = URL(fileURLWithPath: arguments[3]).standardizedFileURL
    let expectedExecutable = destination
        .appendingPathComponent("Contents/MacOS/harness-desktop")
        .standardizedFileURL.path
    guard destination.lastPathComponent == "HarnessKit.app",
          stopExactApplication(
              bundleIdentifier: productBundleIdentifier,
              expectedExecutable: expectedExecutable
          ) else {
        failJSON("owned_application_stop_failed", command)
    }
    emit(["accepted": true, "executable": expectedExecutable])
}

guard !interactiveSessionLocked() else {
    failJSON("interactive_session_locked", command)
}
guard interactiveSessionAvailable() else {
    failJSON("interactive_session_state_unavailable", command)
}

if command == "finder-layout" {
    guard #available(macOS 14.0, *), arguments.count == 3 else {
        failJSON("finder_layout_arguments_invalid", command)
    }
    let mountpoint = arguments[2]
    let sourcePath = URL(fileURLWithPath: mountpoint)
        .appendingPathComponent("HarnessKit.app").standardizedFileURL.path
    let applicationsPath = URL(fileURLWithPath: mountpoint)
        .appendingPathComponent("Applications").standardizedFileURL.path
    var observedLayout: FinderLayout?
    for _ in 0..<50 {
        observedLayout = finderLayout(
            sourcePath: sourcePath,
            applicationsPath: applicationsPath
        )
        if observedLayout != nil { break }
        Thread.sleep(forTimeInterval: 0.1)
    }
    if observedLayout == nil {
        _ = NSWorkspace.shared.selectFile(
            sourcePath,
            inFileViewerRootedAtPath: mountpoint
        )
        for _ in 0..<50 {
            observedLayout = finderLayout(
                sourcePath: sourcePath,
                applicationsPath: applicationsPath
            )
            if observedLayout != nil { break }
            Thread.sleep(forTimeInterval: 0.1)
        }
    }
    guard let layout = observedLayout else {
        failJSON("finder_layout_not_found", command)
    }
    let destination = temporaryScreenshot("dmg-layout")
    _ = NSApplication.shared.setActivationPolicy(.prohibited)
    Task { @MainActor in
        guard let result = await captureWindow(
            layout.windowIdentity,
            destination: destination,
            includeCursor: false,
            extra: finderLayoutPayload(layout, mountpoint: mountpoint)
        ) else { failJSON("finder_layout_capture_failed", command) }
        emit(result)
    }
    RunLoop.main.run()
}

if command == "finder-drag" {
    guard #available(macOS 14.0, *), arguments.count == 5 else {
        failJSON("finder_drag_arguments_invalid", command)
    }
    let sourcePath = URL(fileURLWithPath: arguments[2]).standardizedFileURL.path
    let aliasPath = URL(fileURLWithPath: arguments[3]).standardizedFileURL.path
    let destinationPath = URL(fileURLWithPath: arguments[4]).standardizedFileURL.path
    guard URL(fileURLWithPath: sourcePath).lastPathComponent == "HarnessKit.app",
          URL(fileURLWithPath: aliasPath).lastPathComponent == "Applications",
          let layout = finderLayout(
              sourcePath: sourcePath,
              applicationsPath: aliasPath
          ),
          let finder = runningApplication("com.apple.finder"),
          let source = CGEventSource(stateID: .hidSystemState) else {
        failJSON("finder_drag_identity_mismatch", command)
    }
    guard AXUIElementPerformAction(
        layout.window,
        kAXRaiseAction as CFString
    ) == .success else {
        failJSON("finder_drag_window_raise_failed", command)
    }
    finder.activate(options: [.activateAllWindows])
    let start = CGPoint(x: layout.sourceFrame.midX, y: layout.sourceFrame.midY)
    let end = CGPoint(x: layout.applicationsFrame.midX, y: layout.applicationsFrame.midY)
    var exactHitTargets = false
    var sourceHitMatched = false
    var applicationsHitMatched = false
    var frontmostMatched = false
    for _ in 0..<20 {
        frontmostMatched = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ==
            "com.apple.finder"
        sourceHitMatched = exactFinderItem(
            at: start,
            path: sourcePath,
            finderPID: finder.processIdentifier
        )
        applicationsHitMatched = exactFinderItem(
            at: end,
            path: aliasPath,
            finderPID: finder.processIdentifier
        )
        if frontmostMatched && sourceHitMatched && applicationsHitMatched {
            exactHitTargets = true
            break
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    guard exactHitTargets else {
        failJSON(
            "finder_drag_hit_target_mismatch",
            command,
            evidence: [
                "source_hit_matched": sourceHitMatched,
                "applications_hit_matched": applicationsHitMatched,
                "frontmost_matched": frontmostMatched,
                "source_frame": frameRecord(layout.sourceFrame),
                "applications_frame": frameRecord(layout.applicationsFrame),
            ]
        )
    }
    guard let sourceInventory = bundleInventory(sourcePath), !sourceInventory.isEmpty else {
        failJSON("finder_drag_source_inventory_failed", command)
    }
    let screenshot = temporaryScreenshot("finder-drag-installed")
    _ = NSApplication.shared.setActivationPolicy(.prohibited)
    Task { @MainActor in
        var mousePressed = false
        var mouseUp: CGEvent?
        func releaseMouse() {
            guard mousePressed else { return }
            mouseUp?.post(tap: .cghidEventTap)
            mousePressed = false
        }
        defer {
            releaseMouse()
        }
        guard let move = CGEvent(
            mouseEventSource: source,
            mouseType: .mouseMoved,
            mouseCursorPosition: start,
            mouseButton: .left
        ), let down = CGEvent(
            mouseEventSource: source,
            mouseType: .leftMouseDown,
            mouseCursorPosition: start,
            mouseButton: .left
        ), let up = CGEvent(
            mouseEventSource: source,
            mouseType: .leftMouseUp,
            mouseCursorPosition: end,
            mouseButton: .left
        ) else { failJSON("finder_drag_event_creation_failed", command) }
        mouseUp = up
        move.post(tap: .cghidEventTap)
        down.post(tap: .cghidEventTap)
        mousePressed = true
        usleep(150_000)
        for step in 1...16 {
            let progress = CGFloat(step) / 16
            let point = CGPoint(
                x: start.x + (end.x - start.x) * progress,
                y: start.y + (end.y - start.y) * progress
            )
            guard let drag = CGEvent(
                mouseEventSource: source,
                mouseType: .leftMouseDragged,
                mouseCursorPosition: point,
                mouseButton: .left
            ) else {
                releaseMouse()
                failJSON("finder_drag_event_creation_failed", command)
            }
            drag.post(tap: .cghidEventTap)
            usleep(30_000)
        }
        releaseMouse()
        var consecutiveInventoryMatches = 0
        for _ in 0..<120 {
            if let destinationInventory = bundleInventory(destinationPath),
               destinationInventory == sourceInventory {
                consecutiveInventoryMatches += 1
            } else {
                consecutiveInventoryMatches = 0
            }
            if consecutiveInventoryMatches >= 2 {
                guard var captured = await captureWindow(
                    layout.windowIdentity,
                    destination: screenshot,
                    includeCursor: true,
                    extra: [
                        "source_path": sourcePath,
                        "applications_alias": aliasPath,
                        "destination": destinationPath,
                        "start": [start.x, start.y],
                        "end": [end.x, end.y],
                    ]
                ) else { failJSON("finder_drag_capture_failed", command) }
                captured["installed"] = true
                captured["source_inventory_entries"] = sourceInventory.count
                emit(captured)
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        failJSON("finder_drag_destination_timeout", command)
    }
    RunLoop.main.run()
}

if command == "observe-catalog" || command == "observe-spotlight-name" {
    let surface: String
    let bundleIdentifier: String
    let destination: String
    let query: String
    let captureDestination: URL
    if command == "observe-catalog" {
        guard arguments.count == 7 else { failJSON("search_arguments_invalid", command) }
        surface = arguments[2]
        bundleIdentifier = arguments[3]
        destination = URL(fileURLWithPath: arguments[4]).standardizedFileURL.path
        query = arguments[5]
        captureDestination = URL(fileURLWithPath: arguments[6]).standardizedFileURL
        guard surface == "launchpad" || surface == "spotlight_apps" else {
            failJSON("catalog_surface_unsupported", command)
        }
    } else {
        guard arguments.count == 6 else { failJSON("search_arguments_invalid", command) }
        surface = "spotlight_name"
        bundleIdentifier = arguments[2]
        destination = URL(fileURLWithPath: arguments[3]).standardizedFileURL.path
        query = arguments[4]
        captureDestination = URL(fileURLWithPath: arguments[5]).standardizedFileURL
    }
    let result = surface == "launchpad"
        ? legacyLaunchpadResult(
            query: query,
            bundleIdentifier: bundleIdentifier,
            destination: destination
        )
        : searchResult(
            surface: surface,
            query: query,
            bundleIdentifier: bundleIdentifier,
            destination: destination
        )
    guard let result else {
        emit(["ready": false, "surface": surface])
    }
    guard #available(macOS 14.0, *) else {
        failJSON("screen_capture_api_unavailable", command)
    }
    let observation = searchPayload(
        result,
        surface: surface,
        query: query,
        bundleIdentifier: bundleIdentifier,
        destination: destination
    )
    Task { @MainActor in
        guard let captured = await captureWindow(
            result.window,
            destination: captureDestination,
            includeCursor: false,
            extra: observation
        ) else {
            failJSON(
                "search_observation_capture_failed",
                command,
                evidence: [
                    "surface": surface,
                    "owner_pid": Int(result.window.ownerPID),
                    "window_id": Int(result.window.windowID),
                    "window_layer": result.window.layer,
                    "window_title": result.window.title,
                    "screenshot": captureDestination.path,
                ]
            )
        }
        emit(captured)
    }
    RunLoop.main.run()
}

if command == "launch-result" {
    guard arguments.count == 7 else { failJSON("launch_result_arguments_invalid", command) }
    let sourceName = arguments[2]
    let surface = arguments[3]
    let bundleIdentifier = arguments[4]
    let destination = URL(fileURLWithPath: arguments[5]).standardizedFileURL.path
    let query = arguments[6]
    let expectedExecutable = URL(fileURLWithPath: destination)
        .appendingPathComponent("Contents/MacOS/harness-desktop")
        .standardizedFileURL.path
    guard sourceName == "catalog" || sourceName == "spotlight_name" else {
        failJSON("catalog_launch_source_invalid", command)
    }
    guard (sourceName == "catalog" && (surface == "launchpad" || surface == "spotlight_apps")) ||
        (sourceName == "spotlight_name" && surface == "spotlight_name") else {
        failJSON("catalog_surface_unsupported", command)
    }
    let failurePrefix = sourceName == "catalog" ? "catalog" : "spotlight"
    guard stopExactApplication(
        bundleIdentifier: bundleIdentifier,
        expectedExecutable: expectedExecutable
    ) else {
        failJSON("catalog_launch_preexisting_process_conflict", command)
    }
    let baselinePIDs = Set(
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
            .filter { !$0.isTerminated }
            .map { $0.processIdentifier }
    )
    guard baselinePIDs.isEmpty else {
        failJSON("catalog_launch_preexisting_process_conflict", command)
    }
    let result = surface == "launchpad"
        ? legacyLaunchpadResult(
            query: query,
            bundleIdentifier: bundleIdentifier,
            destination: destination,
            resultPollCount: 120
          )
        : searchResult(
            surface: surface,
            query: query,
            bundleIdentifier: bundleIdentifier,
            destination: destination,
            resultPollCount: 120
          )
    guard let result else {
        failJSON("\(failurePrefix)_result_not_found", command)
    }
    guard activateSearchResult(result, sourceName: sourceName) else {
        failJSON("\(failurePrefix)_result_activation_failed", command)
    }
    for _ in 0..<150 {
        let applications = NSRunningApplication.runningApplications(
            withBundleIdentifier: bundleIdentifier
        ).filter { !$0.isTerminated }
        if let application = applications.first(where: {
            !baselinePIDs.contains($0.processIdentifier) &&
                $0.executableURL?.standardizedFileURL.path == expectedExecutable
        }) {
            let observation = mainWindowCount(application.processIdentifier)
            if observation.0 == 1, let window = observation.1 {
                let frontmost = NSWorkspace.shared.frontmostApplication
                emit([
                    "accepted": true,
                    "executable": expectedExecutable,
                    "main_window_count": observation.0,
                    "pid": Int(application.processIdentifier),
                    "frontmost_bundle_identifier": frontmost?.bundleIdentifier ?? "",
                    "is_active": application.isActive,
                    "spotlight_window_count": spotlightCGWindow() == nil ? 0 : 1,
                    "usable": true,
                    "window_id": Int(window.windowID),
                    "window_layer": window.layer,
                    "window_title": window.title,
                ])
            }
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    failJSON("\(failurePrefix)_launch_not_observed", command)
}

if command == "capture-evidence" {
    guard #available(macOS 14.0, *), arguments.count >= 4 else {
        failJSON("capture_evidence_arguments_invalid", command)
    }
    let mode = arguments[2]
    _ = NSApplication.shared.setActivationPolicy(.prohibited)
    if mode == "window" {
        guard arguments.count == 10,
              let pid = pid_t(arguments[4]),
              let windowID = UInt32(arguments[5]),
              let layer = Int(arguments[7]) else {
            failJSON("capture_evidence_arguments_invalid", command)
        }
        let destination = URL(fileURLWithPath: arguments[8])
        let includeCursor = arguments[9] == "true"
        let exact = ExactWindow(
            bundleIdentifier: arguments[3],
            ownerPID: pid,
            windowID: CGWindowID(windowID),
            title: arguments[6],
            layer: layer
        )
        Task { @MainActor in
            guard let result = await captureWindow(
                exact,
                destination: destination,
                includeCursor: includeCursor
            ) else { failJSON("capture_window_unavailable", command) }
            emit(result)
        }
        RunLoop.main.run()
    }
    if mode == "finder-reveal" {
        guard arguments.count == 4, let (window, itemFrame) = finderRevealWindow() else {
            failJSON("finder_reveal_window_unavailable", command)
        }
        let destination = URL(fileURLWithPath: arguments[3])
        Task { @MainActor in
            guard let result = await captureWindow(
                window,
                destination: destination,
                includeCursor: false,
                extra: ["item_frame": frameRecord(itemFrame)]
            ) else { failJSON("finder_reveal_capture_failed", command) }
            emit(result)
        }
        RunLoop.main.run()
    }
    if mode == "dock" {
        guard arguments.count == 5,
              let (_, itemFrame) = dockItem(named: arguments[3]) else {
            failJSON("dock_item_unavailable", command)
        }
        let destination = URL(fileURLWithPath: arguments[4])
        Task { @MainActor in
            guard let result = await captureMainDisplay(
                destination: destination,
                includeCursor: false,
                extra: ["dock_item_frame": frameRecord(itemFrame)]
            ) else { failJSON("dock_capture_failed", command) }
            emit(result)
        }
        RunLoop.main.run()
    }
    if mode == "app-switcher" {
        guard arguments.count == 5 else {
            failJSON("app_switcher_arguments_invalid", command)
        }
        let destination = URL(fileURLWithPath: arguments[4])
        let source = CGEventSource(stateID: .hidSystemState)
        let commandDown = CGEvent(
            keyboardEventSource: source,
            virtualKey: 55,
            keyDown: true
        )
        commandDown?.flags = .maskCommand
        commandDown?.post(tap: .cghidEventTap)
        usleep(80_000)
        let tabDown = CGEvent(
            keyboardEventSource: source,
            virtualKey: 48,
            keyDown: true
        )
        let tabUp = CGEvent(
            keyboardEventSource: source,
            virtualKey: 48,
            keyDown: false
        )
        tabDown?.flags = .maskCommand
        tabUp?.flags = .maskCommand
        tabDown?.post(tap: .cghidEventTap)
        tabUp?.post(tap: .cghidEventTap)
        usleep(400_000)
        Task { @MainActor in
            defer {
                let commandUp = CGEvent(
                    keyboardEventSource: source,
                    virtualKey: 55,
                    keyDown: false
                )
                commandUp?.post(tap: .cghidEventTap)
            }
            guard let result = await captureMainDisplay(
                destination: destination,
                includeCursor: false,
                extra: ["expected_application": arguments[3]]
            ) else { failJSON("app_switcher_capture_failed", command) }
            emit(result)
        }
        RunLoop.main.run()
    }
    failJSON("capture_evidence_mode_invalid", command)
}

failJSON("observer_command_unsupported", command)
