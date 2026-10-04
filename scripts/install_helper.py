#!/usr/bin/env python3
"""Build and install the independent WeChat Apple Scheduler helper app.

The helper is a tiny signed macOS app. It runs AppleScript in its own process so
WorkBuddy/Codex/Terminal does not need the Apple Events entitlement itself.
"""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BUNDLE_ID = "com.local.apple-chat-scheduler.helper.v3"
APP_NAME = "Apple Chat Scheduler Helper.app"
EXECUTABLE = "AppleChatSchedulerHelper"

SWIFT_SOURCE = r'''
import Foundation

struct HelperResponse: Codable {
    let ok: Bool
    let output: String?
    let errorNumber: Int?
    let errorMessage: String?
}

func writeResponse(_ path: String, _ response: HelperResponse) {
    let url = URL(fileURLWithPath: path)
    do {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONEncoder().encode(response)
        try data.write(to: url, options: .atomic)
    } catch {
        FileHandle.standardError.write(Data("failed to write response: \(error)\n".utf8))
    }
}

func fail(_ jobPath: String?, _ message: String, _ number: Int = -1) -> Never {
    if let jobPath = jobPath {
        // Best-effort: derive the response path from the job JSON.
        if let data = try? Data(contentsOf: URL(fileURLWithPath: jobPath)),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let responsePath = object["response"] as? String {
            writeResponse(responsePath, HelperResponse(ok: false, output: nil, errorNumber: number, errorMessage: message))
        }
    }
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

let args = CommandLine.arguments
let defaultJobPath = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/AppleChatScheduler/job.json")
    .path
let jobPath: String
if let jobIndex = args.firstIndex(of: "--job"), jobIndex + 1 < args.count {
    jobPath = args[jobIndex + 1]
} else {
    jobPath = defaultJobPath
}
guard FileManager.default.fileExists(atPath: jobPath) else {
    fail(jobPath, "job file not found at \(jobPath)")
}
guard let data = try? Data(contentsOf: URL(fileURLWithPath: jobPath)) else {
    fail(jobPath, "cannot read job file")
}
guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
    fail(jobPath, "job file is not valid JSON")
}
guard let script = object["script"] as? String, !script.isEmpty else {
    fail(jobPath, "job does not contain a script")
}
guard let responsePath = object["response"] as? String, !responsePath.isEmpty else {
    fail(jobPath, "job does not contain a response path")
}

let scriptURL = FileManager.default.temporaryDirectory
    .appendingPathComponent(UUID().uuidString)
    .appendingPathExtension("scpt")
try? script.write(to: scriptURL, atomically: true, encoding: .utf8)

defer {
    try? FileManager.default.removeItem(at: scriptURL)
}

let process = Process()
process.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
process.arguments = [scriptURL.path]
let stdoutPipe = Pipe()
let stderrPipe = Pipe()
process.standardOutput = stdoutPipe
process.standardError = stderrPipe

do {
    try process.run()
    process.waitUntilExit()
} catch {
    let message = "failed to launch osascript: \(error)"
    writeResponse(responsePath, HelperResponse(ok: false, output: nil, errorNumber: -1, errorMessage: message))
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

let stdout = String(data: stdoutPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
let stderr = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
if process.terminationStatus != 0 {
    let message = (stderr + stdout).trimmingCharacters(in: .whitespacesAndNewlines)
    writeResponse(responsePath, HelperResponse(ok: false, output: nil, errorNumber: Int(process.terminationStatus), errorMessage: message))
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(process.terminationStatus)
}

let output = stdout.trimmingCharacters(in: .whitespacesAndNewlines)
writeResponse(responsePath, HelperResponse(ok: true, output: output, errorNumber: nil, errorMessage: nil))
'''

INFO_PLIST = {
    "CFBundleDevelopmentRegion": "zh_CN",
    "CFBundleDisplayName": "Apple Chat Scheduler Helper",
    "CFBundleExecutable": EXECUTABLE,
    "CFBundleIdentifier": BUNDLE_ID,
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundleName": "Apple Chat Scheduler Helper",
    "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "1.0.0",
    "CFBundleVersion": "1",
    "LSMinimumSystemVersion": "12.0",
    "NSAppleEventsUsageDescription": "用于控制日历、提醒事项和系统事件，写入日程、待办和图片附件。",
    "NSCalendarsUsageDescription": "用于在 Apple 日历中创建和更新日程。",
    "NSRemindersUsageDescription": "用于在 Apple 提醒事项中创建和更新待办。",
}

ENTITLEMENTS = {
    "com.apple.security.automation.apple-events": True,
}


def run(command: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{proc.stderr or proc.stdout}")


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Applications" / APP_NAME
    target = target.resolve()
    work = Path(tempfile.mkdtemp(prefix="wechat-apple-scheduler-helper-build-"))
    source = work / "main.swift"
    source.write_text(SWIFT_SOURCE, encoding="utf-8")

    app = target
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)

    info_path = app / "Contents" / "Info.plist"
    info_path.write_bytes(plistlib.dumps(INFO_PLIST, fmt=plistlib.FMT_XML))

    entitlements_path = work / "entitlements.plist"
    entitlements_path.write_bytes(plistlib.dumps(ENTITLEMENTS, fmt=plistlib.FMT_XML))

    binary = macos / EXECUTABLE
    run(["xcrun", "swiftc", "-O", "-framework", "Foundation", str(source), "-o", str(binary)])
    run(["codesign", "--force", "--deep", "--sign", "-", "--options", "runtime", "--entitlements", str(entitlements_path), str(app)])

    print(str(app))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
