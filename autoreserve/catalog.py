"""One-time static venue directory; never reads dates or slot availability."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import quote, urlencode, urlsplit

from .config import HOME_URL
from .state import private_json

DIRECTORY_VERSION = 1
DIRECTORY_ENDPOINTS = {"/manage/venue/list", "/manage/venue/queryVenueById", "/system/user/currentUser"}
AVAILABILITY_PATTERN = re.compile(r"/manage/fieldDetail/")
ANY_COURT = "任意可用场地"


# ==========================================
# Function: Signal expired authentication without repeatedly probing the website.
# Method: Keep login failures distinct from directory errors and empty inventories.
# ==========================================
class LoginRequired(RuntimeError):
    pass


# ==========================================
# Function: Block all slot requests during login and directory discovery.
# Method: Abort before transport, including requests triggered by manual navigation.
# ==========================================
def block_availability(page):
    page.route(AVAILABILITY_PATTERN, lambda route: route.abort("blockedbyclient"))


# ==========================================
# Function: Produce a booking route from a cached venue and sport.
# Method: Encode each route segment independently, retaining the verified personal mode.
# ==========================================
def booking_url(venue_id, sport_name):
    return HOME_URL + f"apointmentDetails/1/{quote(venue_id, safe='')}/{quote(sport_name, safe='')}/0"


# ==========================================
# Function: Read only allowlisted static metadata in the authenticated browser.
# Method: Use same-origin fetch with existing cookies and an abort timeout.
# ==========================================
class DirectoryClient:
    # ==========================================
    # Function: Associate metadata reads with an existing browser session.
    # Method: Retain the page without copying cookies or credentials into the catalog.
    # ==========================================
    def __init__(self, page):
        self.page = page

    # ==========================================
    # Function: Execute one strictly read-only directory operation.
    # Method: Reject any non-directory endpoint before evaluating a browser fetch.
    # ==========================================
    def fetch(self, endpoint, values=None):
        if endpoint not in DIRECTORY_ENDPOINTS:
            raise ValueError("目录操作不允许读取空位或提交订单")
        response = self.page.evaluate("""async ({endpoint, body}) => {
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), 15000);
            try {
                const r = await fetch(endpoint, {
                    method: body === null ? 'GET' : 'POST', credentials: 'same-origin',
                    headers: body === null ? {} : {'Content-Type':'application/x-www-form-urlencoded'},
                    body: body, signal: controller.signal,
                });
                const text = await r.text();
                let data; try { data = JSON.parse(text); } catch { data = null; }
                return {status:r.status, data};
            } finally { clearTimeout(timer); }
        }""", {"endpoint": endpoint, "body": urlencode(values) if values is not None else None})
        status, data = response["status"], response["data"]
        if status in {401, 403} or (isinstance(data, dict) and str(data.get("code")) in {"401", "403"}):
            raise LoginRequired("登录已失效，请在网站主界面重新登录并保存")
        if status in {429, 503}:
            raise RuntimeError(f"目录请求 HTTP {status}，已停止读取；请稍后再打开客户端")
        if status != 200 or not isinstance(data, dict):
            raise LoginRequired("未收到已登录网站的有效响应，请重新登录并保存")
        if str(data.get("code")) != "0":
            raise RuntimeError("网站未返回有效目录数据，未写入缓存")
        return data

    # ==========================================
    # Function: Verify server-side authentication before saving or monitoring.
    # Method: Require a user identity from the site's own current-user endpoint.
    # ==========================================
    def authenticated(self):
        data = self.fetch("/system/user/currentUser").get("data")
        if not isinstance(data, dict) or not any(data.get(k) for k in ("loginName", "userName", "userId")):
            raise LoginRequired("尚未完成登录，请先在网站主界面登录")
        return True


# ==========================================
# Function: Cache static venue and sport names once per local account directory.
# Method: Read existing files without network access; atomically publish complete catalogs.
# ==========================================
class DirectoryCache:
    # ==========================================
    # Function: Set the persistent directory location.
    # Method: Keep it under the same private account directory as the login session.
    # ==========================================
    def __init__(self, auth_dir):
        self.path = Path(auth_dir) / "catalog.json"

    # ==========================================
    # Function: Load cached names without expiration checks or implicit refreshes.
    # Method: Reject corrupt or incompatible caches rather than silently fetching again.
    # ==========================================
    def load(self):
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["version"] != DIRECTORY_VERSION or not isinstance(data["venues"], list) or not data["venues"]:
                raise ValueError("invalid catalog")
            ids = set()
            for venue in data["venues"]:
                if not venue["id"] or venue["id"] in ids or not venue["name"] or not isinstance(venue["sports"], list):
                    raise ValueError("invalid venue")
                ids.add(venue["id"])
                for sport in venue["sports"]:
                    if not sport["id"] or not sport["name"]:
                        raise ValueError("invalid sport")
            return data
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("目录缓存格式损坏，未自动访问网站；请移走 .auth/catalog.json 后重建") from exc

    # ==========================================
    # Function: Discover static names only when no cache exists.
    # Method: Page through venues, pace detail reads, and retain no user or slot data.
    # ==========================================
    def ensure(self, fetch, wait, progress=None, stop=None):
        cached = self.load()
        if cached is not None:
            return cached
        stop, progress = stop or (lambda: False), progress or (lambda message: None)
        venues, seen, page_number = [], set(), 1

        # ==========================================
        # Function: Space metadata reads and honor cancellation.
        # Method: Delegate event pumping to the caller without starting slot requests.
        # ==========================================
        def read(endpoint, values):
            if stop():
                raise InterruptedError("目录读取已停止")
            wait()
            if stop():
                raise InterruptedError("目录读取已停止")
            return fetch(endpoint, values)

        while True:
            response = read("/manage/venue/list", {"pageNum": page_number, "pageSize": 36, "flag": 0, "venueName": ""})
            rows, total = response.get("rows"), response.get("total")
            if not isinstance(rows, list) or type(total) not in (int, float) or total < 0:
                raise RuntimeError("场馆目录结构不符，未写入缓存")
            for row in rows:
                identity, name = row.get("venueId"), row.get("venueName")
                if not isinstance(identity, str) or not isinstance(name, str) or not name.strip() or identity in seen:
                    raise RuntimeError("场馆目录重复或缺少名称，未写入缓存")
                seen.add(identity)
                venues.append({"id": identity, "name": name, "campus": row.get("campusName", ""), "sports": []})
            if len(venues) >= total:
                break
            if not rows or page_number >= 100:
                raise RuntimeError("场馆目录分页不完整，未写入缓存")
            page_number += 1
        if not venues:
            raise RuntimeError("网站未返回任何场馆，未写入空目录")
        for index, venue in enumerate(venues, 1):
            progress(f"首次缓存目录 {index}/{len(venues)}：{venue['name']}（不读取空位）")
            detail = read("/manage/venue/queryVenueById", {"id": venue["id"]}).get("data")
            if not isinstance(detail, dict) or not isinstance(detail.get("motionTypes"), list):
                raise RuntimeError(f"{venue['name']} 的项目目录不完整，未写入缓存")
            sports = []
            for item in detail["motionTypes"]:
                if not isinstance(item.get("id"), (str, int)) or not isinstance(item.get("name"), str) or not item["name"].strip():
                    raise RuntimeError("运动项目缺少名称或 ID，未写入缓存")
                sports.append({"id": str(item["id"]), "name": item["name"]})
            venue["sports"] = sports
        result = {"version": DIRECTORY_VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "venues": venues}
        private_json(self.path, result)
        return result
