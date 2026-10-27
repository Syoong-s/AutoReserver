"""Adapter verified against the SJTU site's public Vue render functions.

Only visible DOM is used to choose and click. No internal methods or booking
endpoints are invoked directly. Network responses are observed for confirmation.
"""

from decimal import Decimal
import re
import time
from urllib.parse import urlsplit

from .monitoring import READ_ENDPOINTS, ReadDeferred, AccessDenied
from .catalog import ANY_COURT, LoginRequired

ROOT = "#apointmentDetails"


# ==========================================
# Function: Distinguish an unfinished read from an incompatible booking grid.
# Method: Reuse the scheduler's recoverable read error before any submission.
# ==========================================
class GridNotReady(ReadDeferred):
    pass

SNAPSHOT_JS = """() => {
    const root = document.querySelector('#apointmentDetails');
    if (!root) return null;
    const visible = e => !!(e && e.getClientRects().length);
    const wrapper = root.querySelector('.inner-seat-wrapper');
    const columns = wrapper ? [...wrapper.querySelectorAll('.topsiteStyle')].map(e => e.textContent.trim()) : [];
    const rows = wrapper ? [...wrapper.children].filter(e => [...e.children].some(c => c.classList.contains('seat'))) : [];
    const times = [...root.querySelectorAll('.leftUl li')].map(e => e.textContent.trim());
    return {
        venue: root.querySelector('.topDiv .texts h3')?.textContent.trim() || '',
        sport: root.querySelector('.lists > .el-tabs > .el-tabs__header .el-tabs__item.is-active')?.textContent.trim() || '',
        active_date: root.querySelector('.chart [role=tab].is-active')?.id.replace(/^tab-/, '') || '',
        dates: [...root.querySelectorAll('.chart [role=tab]')].map(e => e.id.replace(/^tab-/, '')),
        loading: [...root.querySelectorAll('.tables .el-loading-mask')].some(visible),
        columns, times,
        rows: rows.map(row => [...row.children].filter(e => e.classList.contains('seat')).map(e => {
            const seat = e.querySelector('.inner-seat');
            return seat?.classList.contains('unselected-seat') ? 'available' :
                   seat?.classList.contains('selected-seat') ? 'selected' : 'unavailable';
        })),
        summary: root.querySelector('.drawerText')?.textContent || '',
        selected_count: root.querySelectorAll('.inner-seat.selected-seat').length,
        total: root.querySelector('.butMoney > span')?.textContent || '',
    };
}"""


# ==========================================
# Function: Normalize displayed order details for exact comparison.
# Method: Remove layout whitespace only.
# ==========================================
def compact(text):
    return re.sub(r"\s+", "", text)


# ==========================================
# Function: Validate the real site's grid before deriving coordinates.
# Method: Check unique columns, hourly row labels, rectangular shape, and date.
# ==========================================
def validate_grid(snapshot):
    if not snapshot or snapshot["loading"]:
        raise GridNotReady("预约网格未加载完成")
    columns, rows, times = snapshot["columns"], snapshot["rows"], snapshot["times"]
    if not columns or len(set(columns)) != len(columns):
        raise RuntimeError("场地列缺失或名称重复，无法可靠选择")
    if not rows or len(rows) > 15 or any(len(row) != len(columns) for row in rows):
        raise RuntimeError("预约网格结构发生变化，请重新检查适配")
    expected = [f"{h:02d}:00" for h in range(7, 23)]
    if times != expected:
        raise RuntimeError("页面时间轴与已核对的 07:00–22:00 小时网格不一致")


# ==========================================
# Function: Find exact available cells for a candidate.
# Method: Use verified hourly row indices and exact column names.
# ==========================================
def candidate_cells(snapshot, target):
    # Closed dates and empty availability responses render no grid at all.
    if snapshot and not snapshot["loading"] and not snapshot["columns"] and not snapshot["rows"]:
        return None
    validate_grid(snapshot)
    if snapshot["active_date"] != target.date or target.court not in snapshot["columns"]:
        return None
    column = snapshot["columns"].index(target.court)
    cells = [(int(slot[:2]) - 7, column) for slot in target.slots]
    if any(row >= len(snapshot["rows"]) or snapshot["rows"][row][col] != "available" for row, col in cells):
        return None
    return cells


# ==========================================
# Function: Resolve the user's unrestricted court choice only after monitoring starts.
# Method: Expand actual snapshot column names in visible order, preserving candidate priority.
# ==========================================
def expand_candidates(snapshot, candidates):
    expanded = []
    for target in candidates:
        if target.court == ANY_COURT:
            from dataclasses import replace
            expanded.extend(replace(target, court=name) for name in (snapshot or {}).get("columns", []))
        else:
            expanded.append(target)
    return expanded


# ==========================================
# Function: Verify the user's requested selection before ordering.
# Method: Compare venue, sport, date, court, every slot, count, and total.
# ==========================================
def verify_selection(snapshot, config, target):
    validate_grid(snapshot)
    if snapshot["venue"] != config.venue or snapshot["sport"] != config.sport:
        raise RuntimeError("场馆或运动项目与配置不一致，已停止")
    if snapshot["active_date"] != target.date or snapshot["selected_count"] != len(target.slots):
        raise RuntimeError("日期或已选数量不符，可能超过场馆时段限制，已停止")
    if target.court not in snapshot["columns"]:
        raise RuntimeError("目标场地列已发生变化，已停止")
    column = snapshot["columns"].index(target.court)
    expected_cells = {(int(slot[:2]) - 7, column) for slot in target.slots}
    selected_cells = {(row, col) for row, values in enumerate(snapshot["rows"])
                      for col, value in enumerate(values) if value == "selected"}
    if selected_cells != expected_cells:
        raise RuntimeError("已选网格位置与目标时段或场地不符，已停止")
    summary = compact(snapshot["summary"])
    if any(compact(token) not in summary for token in [target.date, target.court, *target.slots]):
        raise RuntimeError("右侧订单摘要缺少目标日期、场地或时段，已停止")
    match = re.search(r"[￥¥]\s*(\d+(?:\.\d{1,2})?)\s*元", snapshot["total"])
    limit = Decimal(config.max_total_price)
    if not match or (limit > 0 and Decimal(match[1]) > limit):
        raise RuntimeError("订单总价无法识别或超过 max_total_price，已停止")


# ==========================================
# Function: Operate the SJTU reservation interface.
# Method: Click standard DOM locators and read state after each transition.
# ==========================================
class SjtuPage:
    # ==========================================
    # Function: Associate a browser page with its expected booking configuration.
    # Method: Observe native calendar/inventory requests before navigation or clicks.
    # ==========================================
    def __init__(self, page, config, stop=None):
        self.page = page
        self.config = config
        self.stop = stop or (lambda: False)
        self._pending_reads = set()
        self._read_error = None
        self._read_revision = 0
        page.on("request", self._read_started)
        page.on("requestfinished", self._read_finished)
        page.on("requestfailed", self._read_finished)
        page.on('framenavigated', self._navigated)
        page.on('response',self._read_response)

    # ==========================================
    # Function: Prevent reuse of a settled DOM result across document navigations.
    # Method: Clear the short-lived validation cache whenever the main frame changes.
    # ==========================================
    def _navigated(self, frame):
        if frame == self.page.main_frame:
            self._settled = None
            self._read_revision+=1
            self._pending_reads.clear()
            self._read_error=None

    # ==========================================
    # Function: Track the site's own chained availability reads.
    # Method: Match same-origin calendar and inventory endpoints without reading tokens.
    # ==========================================
    def _read_started(self, request):
        parsed, origin = urlsplit(request.url), urlsplit(self.config.booking_url)
        if ((parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc) and
                any(parsed.path.endswith(path) for path in READ_ENDPOINTS)):
            self._pending_reads.add(request)
            self._read_revision += 1

    # ==========================================
    # Function: Mark native reads complete or failed.
    # Method: Count transitions so a new request restarts the DOM settling window.
    # ==========================================
    def _read_finished(self, request):
        if request in self._pending_reads:
            self._pending_reads.remove(request)
            self._read_revision += 1
            if request.failure and self._read_error is None:
                self._read_error=GridNotReady('场馆数据请求中断，未取得新的空位结果')

    # ==========================================
    # Function: Stop waiting for a DOM update when its data request explicitly failed.
    # Method: Classify only same-origin inventory responses and leave server retry timing to the shared pacer.
    # ==========================================
    def _read_response(self, response):
        parsed,origin=urlsplit(response.url),urlsplit(self.config.booking_url)
        if ((parsed.scheme,parsed.netloc)!=(origin.scheme,origin.netloc) or
                not any(parsed.path.endswith(path) for path in READ_ENDPOINTS)):
            return
        if isinstance(self._read_error,(LoginRequired,AccessDenied)):
            return
        if response.status==401:
            self._read_error=LoginRequired('场馆接口返回 HTTP 401，登录已失效，请重新登录')
        elif response.status==403:
            self._read_error=AccessDenied('场馆接口返回 HTTP 403，已停止监测，请人工检查')
        elif response.status in {429,500,502,503,504}:
            self._read_error=ReadDeferred(f'场馆接口返回 HTTP {response.status}，等待全局退避')

    # ==========================================
    # Function: Release request observers when switching between venue projects.
    # Method: Keep a single-page multi-venue monitor from accumulating old adapters.
    # ==========================================
    def close(self):
        self.page.remove_listener("request", self._read_started)
        self.page.remove_listener("requestfinished", self._read_finished)
        self.page.remove_listener("requestfailed", self._read_finished)
        self.page.remove_listener('framenavigated', self._navigated)
        self.page.remove_listener('response',self._read_response)
        self._pending_reads.clear()

    # ==========================================
    # Function: Return a settled snapshot instead of racing a separate readiness check.
    # Method: Await native reads, overlay removal, expected tabs, and a quiet DOM window.
    # ==========================================
    def loaded_snapshot(self, expected_date=None, expected_sport=None):
        deadline = time.monotonic() + self.config.grid_timeout_seconds
        previous, stable_since = None, None
        reason='预约网格尚未出现'
        while time.monotonic() < deadline:
            if self.stop():
                raise InterruptedError("用户停止监测")
            if self._read_error:
                raise self._read_error
            actual,expected=urlsplit(self.page.url),urlsplit(self.config.booking_url)
            if actual.netloc and (actual.scheme,actual.netloc)!=(expected.scheme,expected.netloc):
                raise LoginRequired('预约页面已跳转离开场馆网站，请重新登录')
            shot = self.snapshot()
            reason=('预约网格尚未出现' if not shot else
                    f'仍有 {len(self._pending_reads)} 个场馆数据请求未完成' if self._pending_reads else
                    '页面加载遮罩尚未消失' if shot['loading'] else
                    '预约日历尚未返回' if not shot['dates'] else
                    '日期或项目尚未切换到目标，或页面仍在更新')
            now = time.monotonic()
            if (shot and shot["dates"] and not shot["loading"] and not self._pending_reads and
                    (expected_date is None or shot["active_date"] == expected_date) and
                    (expected_sport is None or shot["sport"] == expected_sport)):
                state = (shot, self._read_revision)
                if (shot['venue'] == self.config.venue and state == getattr(self, '_settled', None)
                        and now - getattr(self, '_settled_at', 0) < .05):
                    return shot
                if state != previous:
                    previous, stable_since = state, now
                elif now - stable_since >= 0.2:
                    if shot["venue"] != self.config.venue:
                        raise RuntimeError("当前场馆与配置不符，请重新 login 捕获目标场馆")
                    self._settled = state
                    self._settled_at = now
                    return shot
            else:
                previous, stable_since = None, None
            # This pumps Playwright events; it neither refreshes nor requests inventory.
            self.page.wait_for_timeout(50)
        raise GridNotReady(f"预约网格加载超时（等待 {self.config.grid_timeout_seconds:g} 秒；{reason}）")

    # ==========================================
    # Function: Wait until the reservation page is ready.
    # Method: Return the same settled DOM snapshot used to decide readiness.
    # ==========================================
    def ready(self):
        return self.loaded_snapshot()

    # ==========================================
    # Function: Activate the configured sport.
    # Method: Select its exact visible tab and wait for the active class.
    # ==========================================
    def choose_sport(self):
        tab = self.page.locator(ROOT + " .lists > .el-tabs > .el-tabs__header [role=tab]").filter(
            has_text=re.compile(r"^\s*" + re.escape(self.config.sport) + r"\s*$")
        )
        if tab.count() != 1:
            raise RuntimeError("未找到唯一的运动项目 tab，请检查 sport")
        if "is-active" not in (tab.get_attribute("class") or ""):
            self._settled = None
            tab.click()
        from playwright.sync_api import expect
        expect(tab).to_have_class(re.compile(r"\bis-active\b"))
        return self.loaded_snapshot(expected_sport=self.config.sport)

    # ==========================================
    # Function: Switch to a specific ISO booking date.
    # Method: Target Element UI's date tab ID and wait for its loaded panel.
    # ==========================================
    def choose_date(self, value):
        self._read_error=None
        if self._pending_reads:
            self.loaded_snapshot(expected_sport=self.config.sport)
        tab = self.page.locator(ROOT + f' .chart [role="tab"][id="tab-{value}"]')
        if tab.count() == 0:
            return False
        self._settled = None
        tab.click()
        from playwright.sync_api import expect
        expect(tab).to_have_class(re.compile(r"\bis-active\b"))
        return self.loaded_snapshot(expected_date=value, expected_sport=self.config.sport)["active_date"] == value

    # ==========================================
    # Function: Read only non-sensitive scheduling DOM state.
    # Method: Evaluate the public grid and visible order summary.
    # ==========================================
    def snapshot(self):
        return self.page.evaluate(SNAPSHOT_JS)

    # ==========================================
    # Function: Select every required hour for a candidate.
    # Method: Click row/column cells, then verify each selected class.
    # ==========================================
    def select(self, target):
        cells = candidate_cells(self.loaded_snapshot(expected_date=target.date, expected_sport=self.config.sport), target)
        if cells is None:
            return False
        from playwright.sync_api import expect
        for row, column in cells:
            # Rows include only direct children with .seat descendants, excluding the title UL.
            line = self.page.locator(ROOT + " .inner-seat-wrapper > div").filter(has=self.page.locator(".seat")).nth(row)
            cell = line.locator(":scope > .seat > .inner-seat").nth(column)
            expect(cell).to_have_class(re.compile(r"\bunselected-seat\b"))
            cell.click()
            expect(cell).to_have_class(re.compile(r"\bselected-seat\b"))
        verify_selection(self.loaded_snapshot(expected_date=target.date, expected_sport=self.config.sport), self.config, target)
        return True

    # ==========================================
    # Function: Open the booking notice and accept it as requested.
    # Method: Click the exact order button, scope to the visible terms dialog.
    # ==========================================
    def prepare_submit(self, target):
        verify_selection(self.loaded_snapshot(expected_date=target.date, expected_sport=self.config.sport), self.config, target)
        self.page.locator(ROOT + " .butMoney").get_by_role("button", name="立即下单", exact=True).click()
        dialog = self.page.locator('.el-dialog:visible').filter(has_text="本人已认真阅读")
        dialog.wait_for(state="visible")
        # Element UI's native input is zero-sized; click its visible label.
        checkbox = dialog.locator('input[type="checkbox"]')
        if not checkbox.is_checked():
            dialog.locator("label").filter(has_text="本人已认真阅读").click()
        submit = dialog.get_by_role("button", name="提交订单", exact=True)
        from playwright.sync_api import expect
        expect(checkbox).to_be_checked()
        expect(submit).to_be_enabled()
        verify_selection(self.loaded_snapshot(expected_date=target.date, expected_sport=self.config.sport), self.config, target)
        return submit
