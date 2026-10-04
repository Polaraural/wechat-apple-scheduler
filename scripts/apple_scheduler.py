#!/usr/bin/env python3
"""Small macOS helper for idempotent Apple Calendar/Reminders writes.

The script keeps native IDs in a local JSON state file. It never adds source keys,
workflow markers, or paths to user-visible titles or notes.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DEFAULT_STATE = Path.home() / ".codex" / "state" / "wechat-apple-scheduler.json"


HELPER_APP = Path.home() / "Applications" / "Apple Chat Scheduler Helper.app"
HELPER_JOB = Path.home() / "Library" / "Application Support" / "AppleChatScheduler" / "job.json"
HELPER_LOCK = HELPER_JOB.with_suffix(".lock")


def helper_app_path() -> Path | None:
    candidates = [
        Path(os.environ.get("APPLE_CHAT_SCHEDULER_HELPER_APP", "")).expanduser() if os.environ.get("APPLE_CHAT_SCHEDULER_HELPER_APP") else None,
        HELPER_APP,
        Path("/Applications/Apple Chat Scheduler Helper.app"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def process_name(pid: int) -> str:
    try:
        proc = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], text=True, capture_output=True)
    except OSError:
        # Sandboxed hosts (e.g. WorkBuddy) may forbid `ps` entirely. Treat an
        # unreadable process tree as "not detected"; run_applescript() will then
        # fall back to the helper once osascript reports -10004.
        return ""
    return proc.stdout.strip()


def parent_pid(pid: int) -> int:
    try:
        proc = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], text=True, capture_output=True)
    except OSError:
        return 0
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return 0


def running_under_workbuddy() -> bool:
    pid = os.getppid()
    for _ in range(8):
        name = process_name(pid)
        if "WorkBuddy" in name or "workbuddy" in name.lower():
            return True
        pid = parent_pid(pid)
        if pid <= 1:
            break
    return False


def applescript_string(value: str) -> str:
    parts = []
    for part in value.split("\n"):
        escaped = part.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(f'"{escaped}"')
    return " & linefeed & ".join(parts) if parts else '""'


def materialize_applescript(script: str, args: tuple[str, ...]) -> str:
    if "on run argv" not in script:
        return script
    body = script.replace("on run argv", "on main(argv)", 1)
    end_index = body.rfind("end run")
    if end_index == -1:
        raise RuntimeError("AppleScript has on run argv but no matching end run")
    body = body[:end_index] + "end main" + body[end_index + len("end run"):]
    arg_literal = "{" + ", ".join(applescript_string(arg) for arg in args) + "}"
    return body + f"\non run\n    return my main({arg_literal})\nend run\n"


def run_direct(script: str, *args: str) -> str:
    proc = subprocess.run(
        ["osascript", "-", *args],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "osascript failed")
    return proc.stdout.strip()


def run_via_helper(script: str, *args: str, timeout: int = 300) -> str:
    app = helper_app_path()
    if app is None:
        raise RuntimeError(
            "Apple Chat Scheduler Helper is not installed. Run: "
            "python3 scripts/install_helper.py"
        )

    HELPER_JOB.parent.mkdir(parents=True, exist_ok=True)
    with HELPER_LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        response_dir = Path(tempfile.mkdtemp(prefix="wechat-apple-scheduler-response-"))
        response_path = response_dir / "response.json"
        payload = {
            "script": materialize_applescript(script, args),
            "response": str(response_path),
        }
        temp_job = HELPER_JOB.with_suffix(".tmp")
        temp_job.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temp_job, HELPER_JOB)

        # Do not use --args here. On some macOS/open versions --args is not
        # forwarded reliably. The helper reads this fixed-path job file.
        proc = subprocess.run(
            ["open", "-n", str(app)],
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "failed to launch helper")

        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if response_path.exists():
                    response = json.loads(response_path.read_text(encoding="utf-8"))
                    if response.get("ok"):
                        return str(response.get("output", ""))
                    raise RuntimeError(
                        f"helper AppleScript error {response.get('errorNumber')}: {response.get('errorMessage')}"
                    )
                time.sleep(0.2)
            raise RuntimeError(
                "helper timed out. First use requires granting Calendar, Reminders and Accessibility permissions "
                "to Apple Chat Scheduler Helper in System Settings."
            )
        finally:
            try:
                HELPER_JOB.unlink()
            except FileNotFoundError:
                pass


def run_applescript(script: str, *args: str) -> str:
    transport = os.environ.get("APPLE_CHAT_SCHEDULER_TRANSPORT", "auto").lower()
    app = helper_app_path()
    if transport == "helper" or (transport == "auto" and app is not None and running_under_workbuddy()):
        return run_via_helper(script, *args)
    try:
        return run_direct(script, *args)
    except Exception as exc:
        text = str(exc)
        if transport == "auto" and app is not None and any(code in text for code in ("-10004", "-1743", "not authorized", "AppleEvent")):
            return run_via_helper(script, *args)
        raise


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": {}}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"items": {}}
    return json.loads(text)


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


REMINDER_UPSERT = r'''
on dateFromISO(dateText, timeText)
    set theDate to current date
    set year of theDate to (text 1 thru 4 of dateText as integer)
    set month of theDate to (text 6 thru 7 of dateText as integer)
    set day of theDate to (text 9 thru 10 of dateText as integer)
    if timeText is "" then
        set time of theDate to 0
    else
        set time of theDate to ((text 1 thru 2 of timeText as integer) * hours + (text 4 thru 5 of timeText as integer) * minutes)
    end if
    return theDate
end dateFromISO

on run argv
    set listName to item 1 of argv
    set reminderTitle to item 2 of argv
    set reminderBody to item 3 of argv
    set dateText to item 4 of argv
    set timeText to item 5 of argv
    set existingId to item 6 of argv
    set dueDate to my dateFromISO(dateText, timeText)
    tell application "Reminders"
        if not (exists list listName) then make new list with properties {name:listName}
        set destinationList to list listName
        set targetReminder to missing value
        if existingId is not "" then
            try
                set targetReminder to reminder id existingId
            on error
                set targetReminder to missing value
            end try
        end if
        if targetReminder is missing value then
            set targetReminder to make new reminder at end of reminders of destinationList
        end if
        set name of targetReminder to reminderTitle
        set body of targetReminder to reminderBody
        if timeText is "" then
            set allday due date of targetReminder to dueDate
        else
            set due date of targetReminder to dueDate
            set remind me date of targetReminder to dueDate
        end if
        return id of targetReminder
    end tell
end run
'''


EVENT_UPSERT = r'''
on dateFromISO(dateText, timeText)
    set theDate to current date
    set year of theDate to (text 1 thru 4 of dateText as integer)
    set month of theDate to (text 6 thru 7 of dateText as integer)
    set day of theDate to (text 9 thru 10 of dateText as integer)
    if timeText is "" then
        set time of theDate to 0
    else
        set time of theDate to ((text 1 thru 2 of timeText as integer) * hours + (text 4 thru 5 of timeText as integer) * minutes)
    end if
    return theDate
end dateFromISO

on run argv
    set calendarName to item 1 of argv
    set eventTitle to item 2 of argv
    set eventBody to item 3 of argv
    set dateText to item 4 of argv
    set allDayText to item 5 of argv
    set startTimeText to item 6 of argv
    set endTimeText to item 7 of argv
    set repeatText to item 8 of argv
    set alertText to item 9 of argv
    set existingId to item 10 of argv

    set isAllDay to allDayText is "1"
    set startDate to my dateFromISO(dateText, startTimeText)
    if isAllDay then
        set endDate to startDate + 1 * days
    else
        if endTimeText is "" then
            set endDate to startDate + 1 * hours
        else
            set endDate to my dateFromISO(dateText, endTimeText)
        end if
    end if

    tell application "Calendar"
        if not (exists calendar calendarName) then make new calendar with properties {name:calendarName}
        set destinationCalendar to calendar calendarName
        set targetEvent to missing value
        if existingId is not "" then
            try
                set targetEvent to first event of destinationCalendar whose uid is existingId
            on error
                set targetEvent to missing value
            end try
        end if
        if targetEvent is missing value then
            set targetEvent to make new event at end of events of destinationCalendar with properties {summary:eventTitle, description:eventBody, start date:startDate, end date:endDate, allday event:isAllDay}
        else
            set summary of targetEvent to eventTitle
            set description of targetEvent to eventBody
            set start date of targetEvent to startDate
            set end date of targetEvent to endDate
            set allday event of targetEvent to isAllDay
        end if
        if repeatText is "daily" then
            set recurrence of targetEvent to "FREQ=DAILY;INTERVAL=1"
        else
            try
                set recurrence of targetEvent to missing value
            end try
        end if
        if alertText is not "" then
            if (count of display alarms of targetEvent) is 0 then
                tell targetEvent to make new display alarm at end of display alarms with properties {trigger interval:(alertText as integer)}
            end if
        end if
        return uid of targetEvent
    end tell
end run
'''


ATTACH_REMINDER_IMAGE = r'''
on run argv
    set listName to item 1 of argv
    set reminderId to item 2 of argv
    set imagePath to item 3 of argv
    set forceText to item 4 of argv
    set forceAttach to forceText is "1"
    -- Resolve the file reference OUTSIDE any `tell` block. Inside
    -- `tell application "System Events"`, `POSIX file X` is parsed as
    -- `POSIX file X of process "<target>"` and the read fails with -1700.
    set imagePicture to (read (POSIX file imagePath) as JPEG picture)

    tell application "Reminders"
        set targetReminder to reminder id reminderId
        set targetName to name of targetReminder
        activate
        show list listName
    end tell
    delay 0.5

    tell application "System Events"
        tell process "Reminders"
            key code 53
            delay 0.3
            set targetGroup to missing value
            set targetRow to missing value
            set reminderOutline to missing value
            try
                set reminderOutline to outline 1 of scroll area 1 of UI element 3 of splitter group 1 of front window
            on error
                set reminderOutline to outline 1 of scroll area 1 of UI element 1 of splitter group 1 of front window
            end try
            repeat with rowElement in rows of reminderOutline
                try
                    set candidateGroup to group 1 of UI element 1 of rowElement
                    if value of text field 1 of candidateGroup is targetName then
                        set targetGroup to candidateGroup
                        set targetRow to rowElement
                        exit repeat
                    end if
                end try
            end repeat
            if targetGroup is missing value then error "Could not find the target reminder row."
            set selected of targetRow to true
            delay 0.8
            if (count of buttons of targetGroup) is 0 then error "The detail button did not appear for the target reminder."
            click button 1 of targetGroup
            delay 1.0
            set imageTable to table 1 of scroll area 2 of scroll area 4 of pop over 1 of targetGroup
            if (count of rows of imageTable) > 1 and not forceAttach then return "already-attached"
            set the clipboard to imagePicture
            set focused of imageTable to true
            keystroke "v" using command down
            delay 0.8
            if (count of rows of imageTable) < 2 then error "Image attachment did not appear."
            key code 53
            return "attached"
        end tell
    end tell
end run
'''


ATTACH_EVENT_IMAGE = r'''
on run argv
    set calendarName to item 1 of argv
    set eventUid to item 2 of argv
    set imagePath to item 3 of argv
    set forceText to item 4 of argv
    set forceAttach to forceText is "1"
    set imageName to do shell script "basename " & quoted form of imagePath

    tell application "Calendar"
        set targetEvent to first event of calendar calendarName whose uid is eventUid
        set targetTitle to summary of targetEvent
        activate
        show targetEvent
    end tell
    delay 0.5

    tell application "System Events"
        tell process "Calendar"
            key code 53
            key code 53
            keystroke "i" using command down
            delay 0.5
            if exists table 1 of front window then
                if (count of rows of table 1 of front window) > 0 and not forceAttach then
                    key code 53
                    return "already-attached"
                end if
            end if
            click button "添加附件…" of front window
            delay 0.5
            keystroke "g" using {command down, shift down}
            delay 0.5
            set value of text field 1 of sheet 1 of window "打开" to imagePath
            perform action "AXConfirm" of text field 1 of sheet 1 of window "打开"
            delay 0.8
            set fileOutline to outline 1 of scroll area 1 of splitter group 1 of splitter group 1 of window "打开"
            set foundFile to false
            repeat with rowElement in rows of fileOutline
                try
                    if value of text field 1 of UI element 1 of rowElement is imageName then
                        select rowElement
                        set foundFile to true
                        exit repeat
                    end if
                end try
            end repeat
            if not foundFile then error "Could not find " & imageName & " in the open dialog."
            click button "打开" of window "打开"
            delay 0.8
            if not (exists table 1 of front window) then error "Calendar attachment did not appear."
            if (count of rows of table 1 of front window) is 0 then error "Calendar attachment did not appear."
            if exists button "应用" of front window then click button "应用" of front window
            key code 53
            return "attached"
        end tell
    end tell
end run
'''


LIST_REMINDERS = r'''
on run argv
    set listName to item 1 of argv
    tell application "Reminders"
        if not (exists list listName) then return ""
        set outputText to ""
        repeat with r in reminders of list listName
            set outputText to outputText & (id of r) & "|" & (name of r) & "|" & (due date of r as text) & "|" & (remind me date of r as text) & "|" & (body of r) & linefeed
        end repeat
        return outputText
    end tell
end run
'''


LIST_EVENTS = r'''
on run argv
    set calendarName to item 1 of argv
    tell application "Calendar"
        if not (exists calendar calendarName) then return ""
        set outputText to ""
        repeat with e in events of calendar calendarName
            set outputText to outputText & (uid of e) & "|" & (summary of e) & "|" & (start date of e as text) & "|" & (end date of e as text) & "|" & (allday event of e as text) & "|" & (count of display alarms of e) & linefeed
        end repeat
        return outputText
    end tell
end run
'''


def convert_to_jpeg(path: Path) -> Path:
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return path
    tmp = Path(tempfile.gettempdir()) / f"wechat-apple-scheduler-{os.getpid()}.jpg"
    proc = subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(tmp)], capture_output=True, text=True)
    if proc.returncode != 0 or not tmp.exists():
        raise RuntimeError(f"Could not convert {path} to JPEG: {proc.stderr or proc.stdout}")
    return tmp


def cmd_upsert_reminder(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    item = state.setdefault("items", {}).get(args.key, {})
    existing_id = item.get("id", "") if item.get("type") == "reminder" else ""
    native_id = run_applescript(
        REMINDER_UPSERT,
        args.list,
        args.title,
        args.body,
        args.date,
        args.time or "",
        existing_id,
    )
    state["items"][args.key] = {
        "type": "reminder",
        "id": native_id,
        "list": args.list,
        "title": args.title,
        "date": args.date,
        "time": args.time or "",
    }
    save_state(args.state, state)
    print(native_id)


def cmd_upsert_event(args: argparse.Namespace) -> None:
    state = load_state(args.state)
    item = state.setdefault("items", {}).get(args.key, {})
    existing_id = item.get("id", "") if item.get("type") == "event" else ""
    native_id = run_applescript(
        EVENT_UPSERT,
        args.calendar,
        args.title,
        args.body,
        args.date,
        "1" if args.all_day else "0",
        args.start_time or "",
        args.end_time or "",
        args.repeat or "",
        "" if args.alert_minutes is None else str(args.alert_minutes),
        existing_id,
    )
    state["items"][args.key] = {
        "type": "event",
        "id": native_id,
        "calendar": args.calendar,
        "title": args.title,
        "date": args.date,
        "all_day": bool(args.all_day),
        "start_time": args.start_time or "",
        "end_time": args.end_time or "",
        "repeat": args.repeat or "",
    }
    save_state(args.state, state)
    print(native_id)


def cmd_attach_reminder_image(args: argparse.Namespace) -> None:
    print(run_applescript(ATTACH_REMINDER_IMAGE, args.list, args.reminder_id, str(convert_to_jpeg(args.image)), "1" if args.force else "0"))


def cmd_attach_event_image(args: argparse.Namespace) -> None:
    print(run_applescript(ATTACH_EVENT_IMAGE, args.calendar, args.event_uid, str(args.image), "1" if args.force else "0"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    reminder = sub.add_parser("upsert-reminder")
    reminder.add_argument("--state", type=Path, default=DEFAULT_STATE)
    reminder.add_argument("--key", required=True)
    reminder.add_argument("--list", required=True)
    reminder.add_argument("--title", required=True)
    reminder.add_argument("--body", default="")
    reminder.add_argument("--date", required=True, help="YYYY-MM-DD")
    reminder.add_argument("--time", help="HH:MM; omit for all-day")
    reminder.set_defaults(func=cmd_upsert_reminder)

    event = sub.add_parser("upsert-event")
    event.add_argument("--state", type=Path, default=DEFAULT_STATE)
    event.add_argument("--key", required=True)
    event.add_argument("--calendar", required=True)
    event.add_argument("--title", required=True)
    event.add_argument("--body", default="")
    event.add_argument("--date", required=True, help="YYYY-MM-DD")
    event.add_argument("--all-day", action="store_true")
    event.add_argument("--start-time", help="HH:MM")
    event.add_argument("--end-time", help="HH:MM")
    event.add_argument("--repeat", choices=["daily"], help="Only daily is supported in this helper")
    event.add_argument("--alert-minutes", type=int, help="0 means at event time; omit for no explicit alarm")
    event.set_defaults(func=cmd_upsert_event)

    attach_reminder = sub.add_parser("attach-reminder-image")
    attach_reminder.add_argument("--list", required=True)
    attach_reminder.add_argument("--reminder-id", required=True)
    attach_reminder.add_argument("--image", type=Path, required=True)
    attach_reminder.add_argument("--force", action="store_true")
    attach_reminder.set_defaults(func=cmd_attach_reminder_image)

    attach_event = sub.add_parser("attach-event-image")
    attach_event.add_argument("--calendar", required=True)
    attach_event.add_argument("--event-uid", required=True)
    attach_event.add_argument("--image", type=Path, required=True)
    attach_event.add_argument("--force", action="store_true")
    attach_event.set_defaults(func=cmd_attach_event_image)

    reminders = sub.add_parser("list-reminders")
    reminders.add_argument("--list", required=True)
    reminders.set_defaults(func=lambda a: print(run_applescript(LIST_REMINDERS, a.list)))

    events = sub.add_parser("list-events")
    events.add_argument("--calendar", required=True)
    events.set_defaults(func=lambda a: print(run_applescript(LIST_EVENTS, a.calendar)))

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
