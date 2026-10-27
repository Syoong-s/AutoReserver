# AutoReserve

A modern, native desktop reservation client for sports facilities.

[中文说明](README-CN.md) | [Developer Guide](Develop.md)

AutoReserve combines a high-performance GTK 3 desktop interface with an automated Playwright booking worker. It supports single-date reservations, priority-ranked alternatives across multiple venues and courts, weekly recurring schedules, and high-precision timed-release (rush) modes.

> **Important**: AutoReserve monitors availability and automatically submits reservations. It **does not process payments**. Once an order is successfully submitted, please complete payment manually on the official platform before the payment deadline.

---

## Features

- **Native Desktop Interface**: Built with native GTK 3, featuring client-side decorations (CSD), fluid scrolling, keyboard navigation, and adjustable interface font sizes (11–20 px).
- **Multi-Venue & Court Alternatives**: Add multiple venues, projects, and specific courts under a single goal with user-defined priority ordering. The first available alternative is automatically reserved to fulfill the goal.
- **Flexible Court & Time Selection**: Choose specific courts or "any available court", with optional support for merging contiguous time slots into a single reservation.
- **Smart Scheduling & Goal Duplication**: One-click duplication to the following date (`+` button) with independent settings, plus flexible weekly recurring templates (configurable execution weekdays, trigger times, and target date offsets).
- **High-Precision Timed-Release (Rush) Mode**: Tailored for opening-second slot drops. Includes automated session pre-warming (default 45s lead), server clock drift estimation/compensation, and burst polling (250ms interval) during the opening seconds.
- **Real-Time Monitoring & Execution Logs**: Always-visible panels for rolling execution logs (latest 50 lines) and the latest availability snapshot, completely separate from the editor.
- **Clear Status & Native Notifications**: Color-coded sidebar badges (monitoring, weekly repeat, pending, completed, attention needed) and non-blocking native popup dialogs displaying full order details upon successful booking.
- **Safety, Rate Limiting & Duplicate Protection**: Shared global request budgeting and automatic exponential backoff on server load. A persistent local transaction journal prevents duplicate reservations across client restarts.
- **Independent Goals & Order History**: Save/start only the selected goal, retain other drafts, delete completed goals, and review or delete local order records in the sidebar's **历史订单** window.
- **Zero-Cloud Privacy**: Credentials, session tokens, configurations, and order journals remain strictly stored on your local machine.

---

## Quick Start

### 1. Download & Launch

Download the portable archive matching your operating system from the repository's **Releases** page:

| Platform | Package | How to Run |
| --- | --- | --- |
| **Linux x86_64**<br>(Ubuntu 22.04+ / WSLg desktop) | `AutoReserve-xx.xx.xx-linux-x86_64-portable.tar.gz` (or `.zip`) | Extract the entire folder to a writable local directory, then double-click **`AutoReserve`**. |
| **Windows 11 x64** | `AutoReserve-xx.xx.xx-windows-x86_64-portable.zip` | Extract the entire folder to a writable local Windows drive, then double-click **`AutoReserve.exe`**. No Python or WSL installation is required. (See [Windows Guide](docs/WINDOWS.md)). |

> **First Launch Note**: Keep the launcher alongside its extracted files. On the first run, the client will automatically download required runtime components (e.g. browser binaries) to the local folder; an internet connection is required. Settings are stored locally in the `config/` directory.
>
> *(To run from source code, run test suites, or build custom release packages, please refer to the [Developer Guide](Develop.md).)*

### 2. Usage Workflow

1. **Log In**:
   Click **Log in / re-login (登录 / 重新登录)** in the sidebar. In the opened browser window, log in with your jAccount. After logging in, return to the AutoReserve client and click **Save Login (保存登录)**.
2. **Configure Booking Goals**:
   Click **New Goal (新建预约目标)** in the sidebar and enter a target date. Select a venue, project, court, and desired time slots, then click **Add Venue (加入场馆)**. You can add multiple venues/courts as fallback candidates and adjust their priority using the up/down arrows.
3. **(Optional) Enable Weekly Recurrence or Rush Mode**:
   - **Weekly Recurrence**: Click **Set as Repeat Goal (设为重复目标)** to convert the goal into a template, then choose the execution weekdays, trigger time, and target date offset.
   - **Timed-Release (Rush) Mode**: Toggle the **Rush Mode (抢购模式)** switch in the top-right corner. Enter the release date and time (accurate to the second), and configure preparation lead times. (See [Rush Mode Guide](docs/RUSH.md)).
4. **Start Monitoring**:
   Select a goal in the sidebar, click **Save Goal (保存目标)**, then **Start Monitoring (开始监测)**. Both actions apply only to that goal; other drafts remain untouched. Selecting a weekly template starts only that template and its own occurrences. Keep your computer and network connected. When a reservation succeeds, a native notification dialog will pop up. Visit the official portal promptly to pay.

### 3. Remove Goals and Manage Order History

The sidebar **×** removes a target even after an order was submitted. Its draft, monitoring state and owned evidence/cache files are cleaned; its order remains in **历史订单** at the bottom of the sidebar. Deleting a weekly template also removes its generated targets. Deleting one generated occurrence excludes that execution date from future generation without changing the rest of the template.

If an identical venue/court/date/time is blocked by a previous submission, open **历史订单**, review the order number and delete the corresponding **local record** to release its duplicate guard before starting a new goal. This does **not** cancel the website's order. Unrelated records and their duplicate guards remain intact. History can be viewed during monitoring; stop the worker before deleting records. Display and execution preferences remain shared settings; target toolbar actions are scoped to the selection.

---

## License

This project is licensed under the [MIT License](LICENSE).

Third-party dependencies (GTK, Chromium, Playwright, etc.) remain subject to their respective open-source licenses and are not bundled as source code within this repository.
