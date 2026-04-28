"""
政务周报爬虫模块
解析湖北省人民政府门户网站政务周报栏目页和详情页
"""
from __future__ import annotations

import os
import re
import time
import unicodedata
import random
import subprocess
import logging
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from bs4.dammit import UnicodeDammit
from urllib.parse import urljoin, urlparse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

WAF_BLOCK_PATTERNS = [
    r"访问(过于)?频繁",
    r"安全验证",
    r"验证码",
    r"防火墙",
    r"拒绝访问",
    r"forbidden",
    r"access denied",
    r"attention required",
    r"waf",
]

JS_CHALLENGE_PATTERNS = [
    r"\$_ss\.nsd\s*=\s*\d+",
    r'\$_ss\.cd\s*=\s*"[^"]+"',
    r"<script[^>]+r=['\"]m['\"][^>]*>",
]

SNAPSHOT_DIR = Path(__file__).resolve().parent / "logs" / "snapshots"
logger = logging.getLogger(__name__)

# 已知的省级领导姓名列表（可动态扩展）
KNOWN_LEADERS = [
    "王忠林", "李殿勋", "马国强", "孙伟",
    "侯淅珉", "张文兵", "宁咏", "何良军",
    "琚朝晖", "盛阅春", "彭勇", "陈平",
    "黎东辉", "胡亚波", "雷文洁",
]


def fetch_page(url: str) -> str:
    """获取指定URL的HTML内容。

        优先级：
            1. curl-cffi + Node.js JS挑战执行（默认）
            2. curl-cffi 单独
            3. 系统 curl
            4. requests
            5. Selenium + Firefox（可选兜底，默认关闭）
    """
    last_err: Exception | None = None
    last_html: str = ""

    # 1. curl-cffi 先取页面；若遇到JS挑战则调用Node.js执行后再取
    try:
        html = _fetch_page_with_curlffi(url)
        last_html = html
        if _looks_like_js_challenge(html) or _looks_like_waf_blocked(html):
            logger.warning("fetch_page: curl-cffi got challenge page, url=%s", url)
            # 自动/开启兜底模式下，挑战页优先直接走 Selenium，避免 Node 解算失败带来的重试耗时。
            if _should_try_selenium_fallback(html, RuntimeError("js challenge detected")):
                try:
                    logger.warning("fetch_page: direct selenium on challenge, url=%s", url)
                    s_html = _fetch_page_with_selenium_firefox(url)
                    if not _looks_like_js_challenge(s_html) and not _looks_like_waf_blocked(s_html):
                        return s_html
                    last_html = s_html
                except Exception as s_err:
                    logger.warning("fetch_page: direct selenium failed, url=%s, err=%s", url, s_err)
                    if last_err is None:
                        last_err = s_err

            html = _resolve_js_challenge_chain(url, html, use_cffi=True, max_rounds=3)
            last_html = html
        if not _looks_like_js_challenge(html) and not _looks_like_waf_blocked(html):
            return html
        if last_err is None:
            last_err = RuntimeError("命中WAF拦截页（curl-cffi）")
    except _CurlCffiUnavailable:
        # curl-cffi未安装，直接尝试Node.js（用requests取挑战页再解决）
        try:
            html = _fetch_page_with_requests_raw(url)
            last_html = html
            if _looks_like_js_challenge(html) or _looks_like_waf_blocked(html):
                html = _resolve_js_challenge_chain(url, html, use_cffi=False, max_rounds=3)
                last_html = html
            if not _looks_like_js_challenge(html) and not _looks_like_waf_blocked(html):
                return html
            if last_err is None:
                last_err = RuntimeError("命中WAF拦截页（requests-raw）")
        except Exception as e:
            if last_err is None:
                last_err = e
    except Exception as e:
        logger.warning("fetch_page: curl-cffi path failed, url=%s, err=%s", url, e)
        if last_err is None:
            last_err = e

    # 2. curl + Cookie 预热
    for attempt in range(3):
        try:
            html = _fetch_page_with_curl(url)
            last_html = html
            if _looks_like_js_challenge(html):
                html = _resolve_js_challenge_chain(url, html, use_cffi=False, max_rounds=3)
                last_html = html
            if not _looks_like_waf_blocked(html):
                return html
            raise RuntimeError("命中WAF拦截页（curl）")
        except Exception as e:
            logger.warning("fetch_page: curl path failed, url=%s, attempt=%s, err=%s", url, attempt + 1, e)
            if last_err is None:
                last_err = e
            if attempt < 2:
                time.sleep(0.8 + attempt * 1.2 + random.uniform(0, 0.6))

    # 3. requests
    for attempt in range(2):
        try:
            html = _fetch_page_with_requests(url)
            last_html = html
            if _looks_like_js_challenge(html):
                html = _resolve_js_challenge_chain(url, html, use_cffi=False, max_rounds=3)
                last_html = html
            if not _looks_like_waf_blocked(html):
                return html
            raise RuntimeError("命中WAF拦截页（requests）")
        except Exception as e:
            logger.warning("fetch_page: requests path failed, url=%s, attempt=%s, err=%s", url, attempt + 1, e)
            if last_err is None:
                last_err = e
            if attempt < 1:
                time.sleep(1.0 + random.uniform(0, 0.6))

    # 4. 可选 Selenium 兜底（默认关闭，避免影响性能）
    if _should_try_selenium_fallback(last_html, last_err):
        try:
            logger.warning("fetch_page: trying selenium fallback, url=%s", url)
            html = _fetch_page_with_selenium_firefox(url)
            last_html = html
            if not _looks_like_js_challenge(html) and not _looks_like_waf_blocked(html):
                return html
            if last_err is None:
                last_err = RuntimeError("命中WAF拦截页（selenium-firefox）")
        except _SeleniumUnavailable:
            logger.warning("fetch_page: selenium fallback unavailable, url=%s", url)
            if last_err is None:
                last_err = RuntimeError("Selenium兜底已开启，但selenium/firefox/geckodriver不可用")
        except Exception as e:
            logger.warning("fetch_page: selenium fallback failed, url=%s, err=%s", url, e)
            if last_err is None:
                last_err = e

    if last_html:
        snapshot = _dump_html_snapshot("fetch_failed", last_html)
        raise RuntimeError(f"抓取失败: {last_err}; 最后响应快照: {snapshot}")
    raise RuntimeError(f"抓取失败: {last_err}")


def _resolve_js_challenge_chain(url: str, html: str, use_cffi: bool = True, max_rounds: int = 3) -> str:
    """连续执行多轮 JS 挑战，直到拿到非挑战页或达到上限。"""
    current = html
    for _ in range(max_rounds):
        if not _looks_like_js_challenge(current):
            return current
        current = _solve_js_challenge_and_fetch(url, current, use_cffi=use_cffi)
    return current


class _SeleniumUnavailable(Exception):
    pass


def _is_selenium_fallback_enabled() -> bool:
    """是否启用 Selenium 兜底：默认关闭，避免影响抓取性能。"""
    return os.getenv("ENABLE_SELENIUM_FALLBACK", "0").strip().lower() in ("1", "true", "yes", "on")


def _selenium_fallback_mode() -> str:
    """兜底模式: off|auto|on。兼容旧开关 ENABLE_SELENIUM_FALLBACK。"""
    legacy = os.getenv("ENABLE_SELENIUM_FALLBACK")
    if legacy is not None:
        return "on" if legacy.strip().lower() in ("1", "true", "yes", "on") else "off"
    mode = os.getenv("SELENIUM_FALLBACK_MODE", "auto").strip().lower()
    return mode if mode in ("off", "auto", "on") else "auto"


def _should_try_selenium_fallback(last_html: str, last_err: Exception | None) -> bool:
    """仅在必要时触发 Selenium，兼顾成功率和性能。"""
    mode = _selenium_fallback_mode()
    if mode == "on":
        return True
    if mode == "off":
        return False

    # auto: 仅在疑似挑战页/412/WAF场景启用
    err_text = str(last_err or "")
    if last_html and (_looks_like_js_challenge(last_html) or _looks_like_waf_blocked(last_html)):
        return True
    return ("412" in err_text) or ("Precondition Failed" in err_text) or ("WAF" in err_text)


def _fetch_page_with_selenium_firefox(url: str) -> str:
    """使用 Selenium + Firefox 无头浏览器抓取页面。"""
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options as FirefoxOptions
        from selenium.webdriver.firefox.service import Service as FirefoxService
    except ImportError:
        raise _SeleniumUnavailable("selenium 未安装")

    geckodriver_path = os.getenv("GECKODRIVER_PATH")
    firefox_bin = os.getenv("FIREFOX_BIN")

    options = FirefoxOptions()
    options.add_argument("-headless")
    options.set_preference("intl.accept_languages", "zh-CN,zh")
    options.set_preference("general.useragent.override", HEADERS["User-Agent"])
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    if firefox_bin:
        options.binary_location = firefox_bin

    service = FirefoxService(executable_path=geckodriver_path) if geckodriver_path else FirefoxService()
    driver = webdriver.Firefox(service=service, options=options)
    page_timeout = int(os.getenv("SELENIUM_PAGELOAD_TIMEOUT", "20"))
    challenge_timeout = int(os.getenv("SELENIUM_CHALLENGE_WAIT", "10"))
    driver.set_page_load_timeout(page_timeout)

    try:
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}/"
        if url.rstrip("/") != base_url.rstrip("/"):
            try:
                driver.get(base_url)
                time.sleep(1.0)
            except Exception:
                pass

        driver.get(url)

        deadline = time.time() + challenge_timeout
        html = driver.page_source or ""
        while time.time() < deadline and _looks_like_js_challenge(html):
            time.sleep(1.0)
            html = driver.page_source or ""

        return html
    finally:
        driver.quit()


# ── JS挑战执行器（Node.js）──────────────────────────────────────────────────

# Node.js 沙箱：模拟浏览器全局环境，捕获 document.cookie 写入与跳转目标
_NODE_JS_HARNESS = r"""
(function(){
    var vm=require('vm'),fs=require('fs');
    var argv=process.argv;
    var cd=argv[argv.length-1];
    var nsd=parseInt(argv[argv.length-2],10);
    var u=argv[argv.length-3];
    var jsPath=argv[argv.length-4];
    var js=fs.readFileSync(jsPath,'utf8');
  var cookies={},redirect=null;
  function parseCookie(v){var eq=v.indexOf('=');if(eq<0)return;
    var n=v.slice(0,eq).trim(),val=v.slice(eq+1).split(';')[0].trim();
    if(n)cookies[n]=val;}
  var loc={href:u,hash:'',pathname:new URL(u).pathname,
    hostname:new URL(u).hostname,host:new URL(u).host,
    protocol:new URL(u).protocol,search:'',origin:new URL(u).origin,
    replace:function(x){redirect=x;},assign:function(x){redirect=x;},
    reload:function(){}};
  Object.defineProperty(loc,'href',{get:function(){return u;},set:function(x){redirect=x;}});
  var fakeEl={style:{},value:'',innerHTML:'',
    setAttribute:function(){return fakeEl;},getAttribute:function(){return null;},
    appendChild:function(c){return c;},removeChild:function(){},
    insertBefore:function(c){return c;},submit:function(){},
    getElementsByTagName:function(){return[];},querySelectorAll:function(){return[];}};
  var doc={
    get cookie(){return Object.entries(cookies).map(function(e){return e[0]+'='+e[1];}).join('; ');},
    set cookie(v){parseCookie(v);},
    write:function(){},writeln:function(){},open:function(){},close:function(){},
    createElement:function(){return JSON.parse(JSON.stringify(fakeEl));},
    createTextNode:function(t){return{nodeType:3,data:t};},
    getElementById:function(){return null;},
    getElementsByTagName:function(t){
      if(t=='head'||t=='script')return[headEl];return[];},
    querySelector:function(){return null;},
    querySelectorAll:function(){return[];},
    createEvent:function(){return{initEvent:function(){}};},
    dispatchEvent:function(){},
    readyState:'complete',
    head:null,body:null,documentElement:{style:{},getAttribute:function(){return null;}}
  };
  var headEl=JSON.parse(JSON.stringify(fakeEl));
  headEl.appendChild=function(el){
    if(el&&el.src&&redirect===null)redirect=el.src;return el;};
  doc.head=headEl;doc.body=JSON.parse(JSON.stringify(fakeEl));
  var ctx={
    window:null,self:null,top:null,parent:null,
    document:doc,location:loc,
    navigator:{userAgent:'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',platform:'Win32',language:'zh-CN',cookieEnabled:true,onLine:true},
    screen:{width:1920,height:1080,colorDepth:24,availWidth:1920,availHeight:1040},
    history:{pushState:function(){},replaceState:function(){},go:function(){}},
    setTimeout:function(fn,t){try{if(typeof fn==='function')fn();}catch(e){}},
    setInterval:function(){return 0;},clearTimeout:function(){},clearInterval:function(){},
    addEventListener:function(){},removeEventListener:function(){},
    XMLHttpRequest:function(){return{open:function(){},send:function(){},setRequestHeader:function(){},readyState:4,status:200,responseText:''};},
    Image:function(){return{};},ActiveXObject:function(){},
    atob:function(s){return Buffer.from(String(s),'base64').toString('binary');},
    btoa:function(s){return Buffer.from(String(s),'binary').toString('base64');},
    encodeURIComponent:encodeURIComponent,decodeURIComponent:decodeURIComponent,
    encodeURI:encodeURI,decodeURI:decodeURI,escape:escape,unescape:unescape,
    JSON:JSON,Math:Math,Date:Date,parseInt:parseInt,parseFloat:parseFloat,
    isNaN:isNaN,isFinite:isFinite,
    String:String,Number:Number,Boolean:Boolean,Array:Array,
    Object:Object,RegExp:RegExp,Error:Error,Function:Function,
    _ss:null,
    $_ss:{nsd:nsd,cd:cd,lcd:null},
    console:{log:function(){},error:function(){},warn:function(){},dir:function(){}},
    eval:undefined
  };
  ctx.window=ctx;ctx.self=ctx;ctx.top=ctx;ctx.parent=ctx;
  var sandbox=vm.createContext(ctx);
  try{vm.runInContext(js,sandbox,{timeout:6000});}catch(e){}
    try{
        if(ctx.$_ss && typeof ctx.$_ss.lcd==='function'){
            ctx.$_ss.lcd();
        }
    }catch(e){}
  process.stdout.write(JSON.stringify({cookies:cookies,redirect:redirect})+'\n');
})();
"""


def _solve_js_challenge_and_fetch(url: str, challenge_html: str, use_cffi: bool = True) -> str:
    """
    解析安全狗/SafeDog JS挑战页，用 Node.js 执行 challenge JS 生成 Cookie，
    再携带 Cookie 发起真实请求，获取正文 HTML。

    Node.js 未安装时抛出 RuntimeError。
    """
    import shutil
    import json
    import subprocess
    import tempfile

    node_cmd = shutil.which("node") or shutil.which("nodejs")
    if not node_cmd:
        raise RuntimeError("Node.js 未安装，无法执行JS挑战。请 yum install nodejs 后重试。")

    soup = BeautifulSoup(challenge_html, "lxml")
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 找到外部 challenge JS（带 r='m' 属性的 <script src=...>）
    challenge_script_url = None
    for sc in soup.find_all("script", src=True):
        src = sc.get("src", "")
        if sc.get("r") == "m" and src and not src.startswith("http"):
            challenge_script_url = base + src
            break
    if not challenge_script_url:
        raise RuntimeError("未找到challenge script URL，页面结构可能已变")

    # 提取 $_ss 参数
    nsd_m = re.search(r"\$_ss\.nsd\s*=\s*(\d+)", challenge_html)
    cd_m = re.search(r'\$_ss\.cd\s*=\s*"([^"]+)"', challenge_html)
    if not nsd_m or not cd_m:
        raise RuntimeError("未找到$_ss参数")
    nsd, cd = nsd_m.group(1), cd_m.group(1)

    # 下载 challenge JS
    try:
        from curl_cffi.requests import Session as CffiSession
        with CffiSession(impersonate="chrome131") as sess:
            r = sess.get(challenge_script_url, verify=False, timeout=15)
            challenge_js = _decode_bytes(r.content)
    except Exception:
        r = requests.get(challenge_script_url, headers=HEADERS, verify=False, timeout=15)
        challenge_js = _decode_bytes(r.content)

    # 用 Node.js 执行 challenge JS，拿到生成的 Cookie
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
        f.write(challenge_js)
        js_path = f.name

    try:
        proc = subprocess.run(
            [node_cmd, "-e", _NODE_JS_HARNESS, js_path, url, nsd, cd],
            capture_output=True, timeout=12, text=True,
        )
    finally:
        try:
            os.remove(js_path)
        except OSError:
            pass

    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"Node.js执行失败: {proc.stderr[:300]}")

    # 兼容挑战脚本输出杂音，仅解析最后一行 JSON
    out_lines = [line for line in proc.stdout.splitlines() if line.strip()]
    data = json.loads(out_lines[-1]) if out_lines else {}
    new_cookies: dict = data.get("cookies", {})
    if not new_cookies:
        raise RuntimeError("JS挑战未生成Cookie，Node.js执行结果为空")

    target_url = data.get("redirect") or url
    if target_url.startswith("/"):
        target_url = base + target_url

    # 携带Cookie发起真实请求
    if use_cffi:
        try:
            from curl_cffi.requests import Session as CffiSession
            with CffiSession(impersonate="chrome131") as sess:
                for k, v in new_cookies.items():
                    sess.cookies.set(k, v)
                extra = {
                    "Accept": HEADERS["Accept"],
                    "Accept-Language": HEADERS["Accept-Language"],
                    "Referer": base + "/",
                    "Upgrade-Insecure-Requests": "1",
                }
                resp = sess.get(target_url, headers=extra, verify=False, timeout=30, allow_redirects=True)
                return _decode_bytes(resp.content)
        except Exception:
            pass

    req_sess = requests.Session()
    for k, v in new_cookies.items():
        req_sess.cookies.set(k, v)
    resp = req_sess.get(target_url, headers=HEADERS, verify=False, timeout=30)
    resp.raise_for_status()
    return _decode_bytes(resp.content)


def _fetch_page_with_requests_raw(url: str) -> str:
    """不预热，直接用 requests 拿页面（用于取挑战页）。"""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    sess = requests.Session()
    resp = sess.get(url, headers={**HEADERS, "Referer": base_url}, verify=False, timeout=20)
    # 即使 403/412 也保留返回体，用于识别/解算挑战页。
    return _decode_bytes(resp.content)


class _CurlCffiUnavailable(Exception):
    pass


def _fetch_page_with_curlffi(url: str) -> str:
    """使用 curl-cffi 伪装 Chrome TLS/HTTP2 指纹访问目标页。

    安全狗等 WAF 会先下发 JS 挑战页，浏览器执行 JS 生成 Cookie 后再跳转。
    curl-cffi 通过精确伪造 Chrome 的 TLS ClientHello + HTTP/2 指纹，配合
    Session 保持 Cookie，在多数情况下可直接绕过或通过挑战跳转。

    安装：pip install curl-cffi
    CentOS 7 兼容：内置预编译 BoringSSL，无需系统 glibc 2.28+。
    """
    try:
        from curl_cffi.requests import Session as CffiSession
    except ImportError:
        raise _CurlCffiUnavailable("curl-cffi 未安装")

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"

    with CffiSession(impersonate="chrome131") as session:
        extra_headers = {
            "Accept": HEADERS["Accept"],
            "Accept-Language": HEADERS["Accept-Language"],
            "Accept-Encoding": HEADERS["Accept-Encoding"],
            "Cache-Control": HEADERS["Cache-Control"],
            "Pragma": HEADERS["Pragma"],
            "Upgrade-Insecure-Requests": HEADERS["Upgrade-Insecure-Requests"],
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }

        # 预热首页——让 WAF 完成第一次 JS 挑战并写入 Cookie
        if url.rstrip("/") != base_url.rstrip("/"):
            try:
                session.get(
                    base_url,
                    headers={**extra_headers, "Sec-Fetch-Site": "none"},
                    timeout=20,
                    verify=False,
                    allow_redirects=True,
                )
                time.sleep(0.3 + random.uniform(0, 0.3))
            except Exception:
                pass

        # 携带上一步拿到的 Cookie 访问目标页
        resp = session.get(
            url,
            headers={**extra_headers, "Referer": base_url, "Sec-Fetch-Site": "same-origin"},
            timeout=30,
            verify=False,
            allow_redirects=True,
        )
        # 即使 403/412 也保留返回体，用于识别/解算挑战页。
        return _decode_bytes(resp.content)


def _fetch_page_with_curl(url: str) -> str:
    """使用 curl 模拟浏览器请求：先访问站点首页预热 Cookie，再访问目标页。"""
    curl_cmd = "curl.exe" if os.name == "nt" else "curl"
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    cookie_file = os.path.join(os.getenv("TEMP", "/tmp"), "gov_weekly_ofd_cookies.txt")

    common_args = [
        curl_cmd,
        "-k",
        "-sS",
        "-L",
        "--http1.1",
        "--compressed",
        "--connect-timeout",
        "10",
        "--max-time",
        "45",
        "--retry",
        "1",
        "--retry-delay",
        "1",
        "-A",
        HEADERS["User-Agent"],
        "-H",
        f"Accept: {HEADERS['Accept']}",
        "-H",
        f"Accept-Language: {HEADERS['Accept-Language']}",
        "-H",
        f"Accept-Encoding: {HEADERS['Accept-Encoding']}",
        "-H",
        f"Cache-Control: {HEADERS['Cache-Control']}",
        "-H",
        f"Pragma: {HEADERS['Pragma']}",
        "-H",
        f"Upgrade-Insecure-Requests: {HEADERS['Upgrade-Insecure-Requests']}",
        "-H",
        "Sec-Fetch-Dest: document",
        "-H",
        "Sec-Fetch-Mode: navigate",
        "-H",
        "Sec-Fetch-Site: same-origin",
        "-H",
        "Sec-Fetch-User: ?1",
        "-c",
        cookie_file,
        "-b",
        cookie_file,
    ]

    warmup = subprocess.run(
        [*common_args, "-e", base_url, base_url],
        capture_output=True,
        timeout=60,
    )
    if warmup.returncode != 0:
        raise RuntimeError(
            f"curl warmup failed with code {warmup.returncode}: "
            f"{warmup.stderr.decode('utf-8', errors='replace')}"
        )

    referer = base_url if url.rstrip("/") != base_url.rstrip("/") else "https://www.hubei.gov.cn/"
    result = subprocess.run(
        [*common_args, "-e", referer, url],
        capture_output=True,
        timeout=70,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"curl failed with code {result.returncode}: "
            f"{result.stderr.decode('utf-8', errors='replace')}"
        )
    return _decode_bytes(result.stdout)


def _fetch_page_with_requests(url: str) -> str:
    """requests 兜底：保留会话与 Cookie，模拟浏览器访问顺序。"""
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"
    session = requests.Session()

    headers = dict(HEADERS)
    headers.update({
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Connection": "keep-alive",
    })

    session.get(base_url, headers={**headers, "Referer": "https://www.hubei.gov.cn/"}, timeout=20, verify=False)
    resp = session.get(url, headers={**headers, "Referer": base_url}, timeout=30, verify=False)

    # 即使 403/412 也保留返回体，用于识别/解算挑战页。
    content = resp.content
    return _decode_bytes(content)


def _decode_bytes(content: bytes) -> str:
    """尽量稳定地解码响应体，优先使用 UnicodeDammit 自动识别。"""
    guessed = UnicodeDammit(content, is_html=True)
    if guessed.unicode_markup:
        return guessed.unicode_markup

    for enc in ("utf-8", "gbk", "gb2312"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_waf_blocked(html: str) -> bool:
    """粗粒度识别WAF拦截页，便于上层重试和日志提示。"""
    lower = html.lower()
    if "hbgov-newslist-itemheight-18px" in lower:
        return False
    if _looks_like_js_challenge(html):
        return True
    return any(re.search(pat, lower, re.IGNORECASE) for pat in WAF_BLOCK_PATTERNS)


def _looks_like_js_challenge(html: str) -> bool:
    """识别安全狗等JS挑战页特征。"""
    return all(re.search(p, html, re.IGNORECASE) for p in JS_CHALLENGE_PATTERNS)


def parse_column_page(column_url: str) -> list[dict]:
    """
    解析栏目列表页，返回周报列表。
    返回格式: [{"title": "...", "url": "...", "pub_date": "..."}]
    """
    html = fetch_page(column_url)
    soup = BeautifulSoup(html, "lxml")
    results = []

    for li in soup.select("ul.hbgov-newslist-itemheight-18px li"):
        a_tag = li.select_one("a")
        span_tag = li.select_one("span")
        if not a_tag:
            continue
        href = a_tag.get("href", "")
        if not href:
            continue
        full_url = urljoin(column_url, href)
        title = a_tag.get_text(strip=True)
        pub_date = span_tag.get_text(strip=True) if span_tag else ""
        results.append({
            "title": title,
            "url": full_url,
            "pub_date": pub_date,
        })

    if not results:
        results = _extract_reports_fallback(soup, column_url)

    if not results:
        snapshot = _dump_html_snapshot("column_preview_empty", html)
        if _looks_like_waf_blocked(html):
            raise RuntimeError(
                f"栏目页疑似被WAF拦截，请稍后重试或调整服务器IP/请求策略。原始响应已保存: {snapshot}"
            )
        raise RuntimeError(
            f"栏目页解析结果为0，可能是页面结构变化或返回了挑战页。原始响应已保存: {snapshot}"
        )

    return results


def _extract_reports_fallback(soup: BeautifulSoup, column_url: str) -> list[dict]:
    """当主选择器失效时，使用更宽松规则从链接中提取周报列表。"""
    candidates = []
    seen = set()

    for a_tag in soup.select("a[href]"):
        href = (a_tag.get("href") or "").strip()
        if not href or href.startswith("javascript") or href == "#":
            continue

        title = a_tag.get_text(" ", strip=True)
        if not title:
            continue

        is_report_link = (
            ("zwzb" in href.lower() and href.lower().endswith((".shtml", ".html")))
            or ("政务周报" in title)
        )
        if not is_report_link:
            continue

        full_url = urljoin(column_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)

        parent_text = ""
        if a_tag.parent is not None:
            parent_text = a_tag.parent.get_text(" ", strip=True)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", parent_text)
        pub_date = m.group(1) if m else ""

        candidates.append({
            "title": title,
            "url": full_url,
            "pub_date": pub_date,
        })

    return candidates


def _dump_html_snapshot(prefix: str, html: str) -> str:
    """落地问题页面快照，便于线上排查WAF或结构变化。"""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SNAPSHOT_DIR / f"{prefix}_{ts}.html"
    path.write_text(html, encoding="utf-8", errors="ignore")
    return str(path)


def parse_weekly_report(report_url: str) -> dict:
    """
    解析周报详情页，按日期分组提取领导活动。
    返回格式:
    {
        "title": "省政府政务周报（...）",
        "dates": [
            {
                "date": "3月30日",
                "activities": [
                    {
                        "leader": "李殿勋",
                        "summary": "主持召开省政府常务会议...",
                        "detail_url": "https://...",
                    }
                ]
            }
        ]
    }
    """
    html = fetch_page(report_url)
    soup = BeautifulSoup(html, "lxml")

    # 获取标题
    title_tag = soup.select_one("h1") or soup.select_one("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    # 清理标题中的站点后缀
    title = re.sub(r"\s*-\s*湖北省人民政府门户网站.*$", "", title)

    # 获取正文容器
    content_div = (
        soup.select_one(".bt_content")
        or soup.select_one("#myText")
        or soup.select_one(".TRS_Editor")
        or soup.select_one(".article-content")
        or soup.select_one(".hbgov-article-content")
    )
    if not content_div:
        # 兜底：尝试查找包含日期模式的最大容器
        for div in soup.find_all("div"):
            text = div.get_text()
            if re.search(r"\d{1,2}月\d{1,2}日", text) and len(text) > 500:
                content_div = div
                break
    if not content_div:
        return {"title": title, "dates": []}

    # 将HTML内容转为段落列表进行分析
    paragraphs = _extract_paragraphs(content_div)
    dates_data = _group_by_date(paragraphs)

    return {"title": title, "dates": dates_data}


def _extract_paragraphs(container) -> list[dict]:
    """
    从内容容器提取段落信息。
    每个段落包含: text, html, links, is_date_header, leader_name

    去重策略：跳过包含可处理子元素的容器元素，避免父+子重复。
    不使用全局 seen_texts 去重，否则同一段落（如 ►►► 、领导名）在不同日期区间多次出现时会被错误丢弃。
    """
    # 可直接处理的元素类型（语义段落单元）
    PROCESSABLE = {"p", "h1", "h2", "h3", "h4", "td", "th", "li"}
    paragraphs = []

    for elem in container.descendants:
        if elem.name not in PROCESSABLE:
            continue
        # 跳过包含可处理子元素的容器（其内容将由子元素单独处理，避免重复）
        if any(c.name in PROCESSABLE for c in elem.children if hasattr(c, 'name') and c.name):
            continue

        text = elem.get_text(strip=True)
        if not text or len(text) < 2:
            continue

        # 检测日期头
        date_match = re.match(r"^(\d{1,2}月\d{1,2}日)$", text)
        is_date = bool(date_match)

        # 提取链接：优先"详情<<"类，同时保留所有政府文章 URL（.shtml/.html）
        links = []
        for a in elem.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith("javascript") or href == "#":
                continue
            link_text = a.get_text(strip=True)
            if ("详情" in link_text or "<<" in link_text
                    or href.endswith(".shtml") or href.endswith(".html")):
                links.append(href)

        paragraphs.append({
            "text": text,
            "html": str(elem),
            "links": links,
            "is_date_header": is_date,
            "date_value": date_match.group(1) if date_match else None,
        })

    return paragraphs


def _group_by_date(paragraphs: list[dict]) -> list[dict]:
    """将段落按日期分组，提取领导活动"""
    dates = []
    current_date = None
    current_activities = []
    buffer_texts = []
    buffer_links = []
    current_leader_hint = None  # 由 ►► 领导名 行设置，作为 _identify_leader 的兜底

    def flush_buffer():
        nonlocal buffer_texts, buffer_links
        if not buffer_texts:
            return
        combined = "\n".join(buffer_texts)
        leader = current_leader_hint
        if leader:
            # 提取概览标题：第一条短文本（不以日期开头、不含"详情"）
            overview = ""
            if buffer_texts:
                first = buffer_texts[0].strip()
                first = re.sub(r'\s*详情\s*[<＜《]+.*$', '', first).strip()
                if not re.match(r'^\d{1,2}月\d{1,2}日', first):
                    overview = first
            current_activities.append({
                "leader": leader,
                "summary": combined,
                "overview_title": overview,
                "detail_url": buffer_links[0] if buffer_links else "",
            })
        buffer_texts = []
        buffer_links = []

    # ► 或 ►► 标记是活动块分隔符（一个或多个 ►）
    arrow_pattern = re.compile(r"^►+")

    for para in paragraphs:
        text = para["text"]

        if para["is_date_header"]:
            flush_buffer()
            if current_date and current_activities:
                dates.append({"date": current_date, "activities": current_activities})
            current_date = para["date_value"]
            current_activities = []
            buffer_texts = []
            buffer_links = []
            current_leader_hint = None
            continue

        if current_date is None:
            continue

        # 检测 ►+ 开头：分隔符，箭头后可能直接跟着领导名（如 "►► 李殿勋"）
        if arrow_pattern.match(text):
            flush_buffer()
            remainder = arrow_pattern.sub("", text).strip()
            if remainder in KNOWN_LEADERS:
                # 纯领导名：只更新 hint，不写入 buffer（避免重复条目）
                current_leader_hint = remainder
            elif remainder:
                # 箭头后跟的是活动描述，直接进 buffer
                buffer_texts.append(remainder)
                buffer_links.extend(para["links"])
            else:
                # 纯箭头分隔符（无名字），重置 hint
                current_leader_hint = None
            continue

        # 检测独立领导名行（单独成段的领导姓名，如 "李殿勋"）
        stripped = text.strip()
        if stripped in KNOWN_LEADERS:
            flush_buffer()
            current_leader_hint = stripped  # 设置 hint，不写入 buffer
            continue

        # 检测以领导名开头的新标题行（通常较长，包含活动描述）
        starts_with_leader = False
        for name in KNOWN_LEADERS:
            if text.startswith(name) and len(text) > len(name) + 2:
                starts_with_leader = True
                break

        if starts_with_leader and buffer_texts:
            flush_buffer()

        # 跳过纯图片或编辑信息行
        if re.match(r"^(编辑|责编|审核|扫一扫)[:：]", text):
            continue

        buffer_texts.append(text)
        buffer_links.extend(para["links"])

        # "详情<<"类链接段落标志一条活动的结束（参与人员+详情链接行）
        # 立即 flush，但保留 current_leader_hint，以便同一领导的下一条活动继续归属
        if para["links"] and buffer_texts and ("详情" in text or "<<" in text):
            flush_buffer()

    # 收尾
    flush_buffer()
    if current_date and current_activities:
        dates.append({"date": current_date, "activities": current_activities})

    return dates


def _identify_leader(text: str) -> str | None:
    """从文本中识别领导姓名"""
    for name in KNOWN_LEADERS:
        if name in text:
            return name
    return None


def fetch_article_content(url: str) -> dict:
    """
    获取详情文章的正文内容。
    返回: {"title": "...", "content": "...", "pub_date": "..."}
    """
    html = fetch_page(url)
    soup = BeautifulSoup(html, "lxml")

    title_tag = soup.select_one("h1") or soup.select_one("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    title = re.sub(r"\s*-\s*湖北省人民政府门户网站.*$", "", title)

    # 发布时间
    pub_date = ""
    date_span = soup.find(string=re.compile(r"\d{4}-\d{2}-\d{2}"))
    if date_span:
        m = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})", date_span.get_text())
        if m:
            pub_date = m.group(1).strip()

    # 正文
    content_div = (
        soup.select_one(".bt_content")
        or soup.select_one("#myText")
        or soup.select_one(".TRS_Editor")
        or soup.select_one(".article-content")
        or soup.select_one(".hbgov-article-content")
    )

    content_paragraphs = []
    if content_div:
        # 优先取 <p> 标签保留原始段落结构
        p_tags = content_div.find_all("p")
        if p_tags:
            for p in p_tags:
                text = p.get_text(strip=True)
                if not text or len(text) <= 2:
                    continue
                # 过滤编辑/审核等署名信息
                if re.match(r"^(编辑|责编|审核|扫一扫|来源|（编辑|（责编|（审核)", text):
                    continue
                # 过滤末尾短署名行（纯中文人名，含全角空格，如「姚　盼」）
                stripped = re.sub(r'[\s\u3000]+', '', text)
                if len(stripped) <= 4 and re.match(r'^[\u4e00-\u9fff]+$', stripped):
                    continue
                # 过滤"图解：..."等附加信息行
                if re.match(r"^图解[：:]", text):
                    continue
                # 过滤记者署名，如（肖丽琼）（湖北日报记者邓伟）
                if re.match(r'^[（(].*?记者.*?[）)]$', text) or re.match(r'^[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]$', text):
                    continue
                # 检测对齐方式
                style = p.get("style", "")
                align = "center" if "text-align: center" in style or "text-align:center" in style else "left"
                content_paragraphs.append({"text": text, "align": align})
        else:
            # 无 <p> 标签时，用 <br> 拆分
            raw_text = content_div.get_text(separator="\n")
            for line in raw_text.split("\n"):
                line = line.strip()
                if not line or len(line) <= 2:
                    continue
                if re.match(r"^(编辑|责编|审核|扫一扫|来源)", line):
                    continue
                if re.match(r"^图解[：:]", line):
                    continue
                # 过滤记者署名
                if re.match(r'^[（(].*?记者.*?[）)]$', line) or re.match(r'^[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]$', line):
                    continue
                content_paragraphs.append({"text": line, "align": "left"})

    # 从正文开头提取居中段落作为真正标题（舍弃 <h1> 标题）
    body_title_parts = []
    while content_paragraphs:
        first = content_paragraphs[0]
        if isinstance(first, dict) and first.get("align") == "center":
            body_title_parts.append(first["text"])
            content_paragraphs.pop(0)
        else:
            break
    if body_title_parts:
        title = "".join(body_title_parts)

    # 清理末尾段落中拼接的记者署名，如 "...发展。（湖北日报记者邓伟）"
    if content_paragraphs:
        last = content_paragraphs[-1]
        if isinstance(last, dict):
            cleaned = re.sub(r'[（(][^）)]*?记者[^）)]*?[）)]\s*$', '', last["text"]).strip()
            cleaned = re.sub(r'[（(][\u4e00-\u9fff\s\u3000]{1,8}[）)]\s*$', '', cleaned).strip()
            if cleaned:
                content_paragraphs[-1] = {**last, "text": cleaned}
            else:
                content_paragraphs.pop()

    content = "\n\n".join(
        p["text"] if isinstance(p, dict) else p for p in content_paragraphs
    )

    # 清洗标题：把所有 Unicode 空格（Zs 类）归一为普通空格，去除控制字符。
    title = "".join(
        " " if (unicodedata.category(ch) == "Zs") else ch
        for ch in title
        if unicodedata.category(ch) not in {"Cc", "Cf"}
    )
    title = re.sub(r" {2,}", " ", title).strip()

    return {"title": title, "content": content, "pub_date": pub_date, "paragraphs": content_paragraphs}


if __name__ == "__main__":
    # 测试用
    import json

    url = "https://www.hubei.gov.cn/hbfb/zwzb/index.shtml"
    reports = parse_column_page(url)
    print(f"找到 {len(reports)} 篇周报:")
    for r in reports[:3]:
        print(f"  {r['title']} - {r['pub_date']}")

    if reports:
        print(f"\n解析第一篇: {reports[0]['url']}")
        data = parse_weekly_report(reports[0]["url"])
        print(json.dumps(data, ensure_ascii=False, indent=2))
