import { useState, useEffect, useCallback, useRef, Component } from "react";


// ISBN-10 → ISBN-13 dönüşümü (eBay GTIN araması için)
const toIsbn13 = (isbn) => {
  const s = (isbn||"").replace(/[^0-9X]/gi,"").toUpperCase();
  if (s.length === 13) return s;
  if (s.length !== 10) return s;
  const core = "978" + s.slice(0,9);
  let total = 0;
  for (let i = 0; i < core.length; i++)
    total += parseInt(core[i]) * (i % 2 === 0 ? 1 : 3);
  const check = (10 - (total % 10)) % 10;
  return core + check;
};

const BASE = import.meta.env.PROD ? "" : "/api";
const req = async (path, opts = {}, timeoutMs = 15000) => {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(BASE + path, { headers: { "Content-Type": "application/json" }, signal: ctrl.signal, ...opts });
    if (!r.ok) { const e = await r.json().catch(() => ({ detail: r.statusText })); throw new Error(e.detail || "API error"); }
    return r.json();
  } catch (e) {
    if (e.name === "AbortError") throw new Error("İstek zaman aşımına uğradı (>15s)");
    throw e;
  } finally { clearTimeout(timer); }
};

// ── ErrorBoundary — prevents white screen on any JS exception ──────────────
class ErrorBoundary extends Component {
  constructor(p) { super(p); this.state = { err: null }; }
  static getDerivedStateFromError(err) { return { err }; }
  componentDidCatch(err, info) { console.error("[TB ErrorBoundary]", err, info); }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div style={{
        minHeight:"100vh",background:"#0a0a0f",color:"#e2e2e2",
        display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",
        fontFamily:"monospace",padding:32,gap:16,
      }}>
        <div style={{fontSize:32}}>⚠️</div>
        <div style={{fontSize:16,fontWeight:700,color:"#f87171"}}>UI Error</div>
        <div style={{fontSize:12,color:"#888",maxWidth:480,textAlign:"center",wordBreak:"break-word"}}>
          {String(this.state.err?.message || this.state.err)}
        </div>
        <button
          onClick={()=>window.location.reload()}
          style={{marginTop:8,padding:"8px 24px",background:"#f0a500",color:"#000",border:"none",borderRadius:6,fontFamily:"monospace",fontSize:13,fontWeight:700,cursor:"pointer"}}
        >
          ↺ Reload
        </button>
        <details style={{fontSize:10,color:"#444",maxWidth:560,wordBreak:"break-word"}}>
          <summary style={{cursor:"pointer",color:"#555"}}>stack trace</summary>
          <pre style={{marginTop:8,whiteSpace:"pre-wrap"}}>{this.state.err?.stack}</pre>
        </details>
      </div>
    );
  }
}

const DARK = {
  bg: "#0a0a0f", surface: "#0d0d14", surface2: "#111117", border: "#1a1a2e", border2: "#161622",
  text: "#e2e2e2", muted: "#555", muted2: "#444", muted3: "#333",
  accent: "#f0a500", accentHover: "#fbbf24", accentText: "#0a0a0f",
  inputBg: "#111827", inputBorder: "#1f2937", rowBg: "#0d0d14", rowBorder: "#161622",
  cardBg: "#111117", cardBorder: "#1a1a2e",
  green: "#34d399", blue: "#60a5fa", purple: "#a78bfa", orange: "#fb923c", red: "#f87171",
};
const LIGHT = {
  bg: "#f5f4ef", surface: "#ffffff", surface2: "#faf9f5", border: "#e0ddd3", border2: "#ece9df",
  text: "#1a1a14", muted: "#8a8880", muted2: "#a0a09a", muted3: "#c0c0ba",
  accent: "#c47c0e", accentHover: "#e8961a", accentText: "#ffffff",
  inputBg: "#ffffff", inputBorder: "#d4d1c6", rowBg: "#ffffff", rowBorder: "#ece9df",
  cardBg: "#ffffff", cardBorder: "#e0ddd3",
  green: "#16a34a", blue: "#2563eb", purple: "#7c3aed", orange: "#ea580c", red: "#dc2626",
};
// Soft gündüz teması — düşük kontrast, göz dostu, slate-mavi tonlar
const SOFT = {
  bg: "#e8edf2", surface: "#f0f4f8", surface2: "#dde4ec", border: "#c4cdd8", border2: "#d0d9e4",
  text: "#1e2a38", muted: "#5a6a7a", muted2: "#7a8a9a", muted3: "#9aaabb",
  accent: "#3d7ab5", accentHover: "#2e6aa5", accentText: "#ffffff",
  inputBg: "#f0f4f8", inputBorder: "#b8c4d0", rowBg: "#f0f4f8", rowBorder: "#d0d9e4",
  cardBg: "#f0f4f8", cardBorder: "#c4cdd8",
  green: "#2d8a5e", blue: "#2e6da4", purple: "#6b4fa8", orange: "#c2620a", red: "#b83232",
};

const BUILD_ID = "2026-03-02-v20-soft-theme";
// Build zamanı — Vite tarafından inject edilir (her npm run build'de güncellenir)
const BUILD_TIME = (() => {
  try {
    const iso = __BUILD_TIME__;
    const d = new Date(iso);
    // New York saati (ET — DST otomatik)
    const fmt = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false,
    });
    const parts = Object.fromEntries(fmt.formatToParts(d).map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} ET`;
  } catch { return ""; }
})();

const dollar = (v) => v != null ? `$${Math.round(v)}` : "—";
const isbn13to10 = (isbn) => {
  const s = String(isbn).replace(/[^0-9X]/gi,'');
  if (s.length === 10) return s; // already ISBN-10
  if (s.length !== 13 || (!s.startsWith('978') && !s.startsWith('979'))) return s;
  const core = s.slice(3, 12);
  const total = core.split('').reduce((sum, d, i) => sum + (10-i)*parseInt(d), 0);
  const check = (11 - (total % 11)) % 11;
  return core + (check === 10 ? 'X' : String(check));
};
const fmtSecs = (s) => { if (!s || isNaN(s) || !isFinite(s)) return null; if (s >= 86400) return `${Math.round(s/86400)}G`; if (s >= 3600) return `${Math.round(s/3600)}s`; if (s >= 60) return `${Math.round(s/60)}d`; return `${s}sn`; };
// ─── eBay search URL builder ─────────────────────────────────────────────────
// SINGLE SOURCE OF TRUTH for all "Open on eBay" links in this panel.
// If eBay changes URL params: update only this function, rebuild dist, deploy.
//
// Params:
//   isbn      : ISBN-13 (digits only preferred)
//   condition : optional bucket key (brand_new | like_new | very_good | good | acceptable)
//   sort      : "cheapest" (default) | "none"
//
// Current param strategy (last verified: 2026-02):
//   _sacat=267        Books category
//   LH_BIN=1          Buy It Now only
//   rt=nc             refine + no cache (freshness)
//   _sop=15           Sort: Price + Shipping, lowest first
//   LH_ItemCondition  condition filter (best-effort, omit if unknown bucket)
//
// COND_IDS: eBay Books condition IDs — may drift with UI deploys.
// If links return wrong condition results, update values here.
const _EBAY_COND_IDS = {
  brand_new:  "1000",
  like_new:   "3000",
  very_good:  "4000",
  good:       "5000",
  acceptable: "6000",
  // used_all → no condition filter (shows all used)
};

function buildEbaySearchUrl({ isbn, condition = null, sort = "cheapest" } = {}) {
  if (!isbn) return "#";
  const params = new URLSearchParams({
    _nkw:   isbn,
    _sacat: "267",
    // LH_BIN removed: including Best Offer (Accepts Offers) listings alongside BIN
    LH_BO:  "1",        // Best Offer included
    rt:     "nc",
    _sop:   sort === "cheapest" ? "15" : "12",
  });
  const cid = condition && _EBAY_COND_IDS[condition];
  if (cid) params.set("LH_ItemCondition", cid);
  return `https://www.ebay.com/sch/i.html?${params.toString()}`;
}

// Unit-like smoke tests (run once at module load, log any mismatch)
;(() => {
  const cases = [
    { input: { isbn: "9780132350884" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780132350884&_sacat=267&LH_BO=1&rt=nc&_sop=15" },
    { input: { isbn: "9780132350884", condition: "good" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780132350884&_sacat=267&LH_BO=1&rt=nc&_sop=15&LH_ItemCondition=5000" },
    { input: { isbn: "9780974769431", condition: "like_new", sort: "cheapest" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780974769431&_sacat=267&LH_BO=1&rt=nc&_sop=15&LH_ItemCondition=3000" },
  ];
  cases.forEach(({ input, expect }) => {
    const got = buildEbaySearchUrl(input);
    if (got !== expect) console.warn("[buildEbaySearchUrl] MISMATCH", { input, got, expect });
  });
})();

// Telemetry: report broken eBay link (fire-and-forget)
async function reportBrokenLink({ isbn, url, context }) {
  try {
    await fetch(BASE + "/telemetry/link-broken", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ isbn, url, context, build_id: BUILD_ID, userAgent: navigator.userAgent }),
    });
  } catch (_) { /* non-fatal */ }
}
const cleanIsbn = (s) => (s || "").replace(/[^0-9Xx]/g, "").toUpperCase();
const validateIsbn = (s) => {
  const c = cleanIsbn(s);
  if (c.length === 13) {
    const sum = c.split("").reduce((acc, ch, i) => acc + parseInt(ch) * (i % 2 === 0 ? 1 : 3), 0);
    return sum % 10 === 0;
  }
  if (c.length === 10) {
    let sum = 0;
    for (let i = 0; i < 9; i++) sum += parseInt(c[i]) * (10 - i);
    const last = c[9] === "X" ? 10 : parseInt(c[9]);
    return (sum + last) % 11 === 0;
  }
  return false;
};
const parseSecs = (str) => { const m = String(str).trim().match(/^(\d+(?:\.\d+)?)(d|h|m|s)?$/i); if (!m) return null; const n = parseFloat(m[1]), u = (m[2]||"h").toLowerCase(); return Math.round(u==="d"?n*86400:u==="h"?n*3600:u==="m"?n*60:n); };
const fmtTime = (unix) => unix ? new Date(unix*1000).toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit"}) : "—";

function useToast() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((msg, type="info") => { const id=Date.now()+Math.random(); setToasts(t=>[...t,{id,msg,type}]); setTimeout(()=>setToasts(t=>t.filter(x=>x.id!==id)),3200); }, []);
  return { toasts, push };
}

// OpenLibrary metadata cache: title + author + year, localStorage with 7-day TTL
const OL_CACHE_KEY = "ol_meta_v2";
const OL_TTL_MS = 7 * 24 * 3600 * 1000;

function _olCacheLoad() {
  try { return JSON.parse(localStorage.getItem(OL_CACHE_KEY) || "{}"); } catch { return {}; }
}
function _olCacheSave(data) {
  try { localStorage.setItem(OL_CACHE_KEY, JSON.stringify(data)); } catch {}
}

// Returns { [isbn]: { title, author, year } | null }
function useBookMeta(isbns) {
  const [meta, setMeta] = useState(() => {
    const raw = _olCacheLoad();
    const now = Date.now();
    // Strip expired entries on load
    const valid = {};
    for (const [isbn, entry] of Object.entries(raw)) {
      if (entry && entry._ts && (now - entry._ts) < OL_TTL_MS) valid[isbn] = entry;
    }
    return valid;
  });

  const isbnKey = isbns.join(",");
  useEffect(() => {
    const now = Date.now();
    const missing = isbns.filter(isbn =>
      meta[isbn] === undefined ||
      (meta[isbn]?._ts && (now - meta[isbn]._ts) >= OL_TTL_MS)
    );
    if (!missing.length) return;

    // Mark as in-flight (null) to avoid duplicate fetches
    setMeta(m => {
      const n = {...m};
      missing.forEach(i => { if (n[i] === undefined) n[i] = null; });
      return n;
    });

    missing.forEach(isbn => {
      fetch(`https://openlibrary.org/isbn/${isbn}.json`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          // author_name is in /works/ — use subtitle/by_statement if present, else skip
          const title  = d?.title || "";
          const author = d?.by_statement?.replace(/\s*\/.*$/, "").trim() || "";
          const year   = d?.publish_date ? String(d.publish_date).match(/\d{4}/)?.[0] || "" : "";
          const entry  = { title, author, year, _ts: Date.now() };
          setMeta(m => {
            const n = {...m, [isbn]: entry};
            _olCacheSave(n);
            return n;
          });
        })
        .catch(() => {
          setMeta(m => {
            const n = {...m, [isbn]: { title:"", author:"", year:"", _ts: Date.now() }};
            _olCacheSave(n);
            return n;
          });
        });
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isbnKey]);

  return meta;
}

// Convenience: map isbn → title string (backward compat)
function titlesFromMeta(meta) {
  const out = {};
  for (const [isbn, m] of Object.entries(meta)) out[isbn] = m?.title ?? null;
  return out;
}

function ToastStack({ toasts, C }) {
  return (
    <div style={{position:"fixed",bottom:24,right:24,display:"flex",flexDirection:"column",gap:8,zIndex:999}}>
      {toasts.map(t=>(
        <div key={t.id} style={{padding:"10px 16px",borderRadius:6,fontSize:12,minWidth:200,fontFamily:"var(--mono)",animation:"slideIn .18s ease",background:t.type==="success"?"#0f1f0f":t.type==="error"?"#1f0f0f":C.surface2,borderLeft:`3px solid ${t.type==="success"?C.green:t.type==="error"?C.red:C.border}`,color:t.type==="success"?C.green:t.type==="error"?C.red:C.muted,boxShadow:"0 4px 12px rgba(0,0,0,.15)"}}>
          {t.type==="success"?"✓ ":t.type==="error"?"✗ ":""}{t.msg}
        </div>
      ))}
    </div>
  );
}

function StatCard({ icon, label, value, sub, accent, C }) {
  return (
    <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:"20px 24px",position:"relative",overflow:"hidden"}}>
      <div style={{position:"absolute",top:0,left:0,right:0,height:2,background:`linear-gradient(90deg,${accent},transparent)`}}/>
      <div style={{fontSize:20,marginBottom:8}}>{icon}</div>
      <div style={{fontSize:30,fontWeight:600,color:C.text,lineHeight:1}}>{value}</div>
      <div style={{fontSize:11,color:C.muted,marginTop:5}}>{label}</div>
      <div style={{fontSize:10,color:C.muted3,marginTop:2}}>{sub}</div>
    </div>
  );
}

function ST({ children, C, style }) {
  return <div style={{fontSize:11,letterSpacing:"0.03em",color:C.muted,marginBottom:12,...style}}>{children}</div>;
}

function PeriodBar({ label, avg, count, weight, C, isBase }) {
  return (
    <div style={{display:"flex",alignItems:"center",gap:10,padding:"8px 0",borderBottom:`1px solid ${C.border}`}}>
      <div style={{width:60,fontSize:10,color:C.muted,flexShrink:0}}>{label}</div>
      <div style={{flex:1,height:6,background:C.surface2,borderRadius:3,overflow:"hidden"}}>
        {avg && <div style={{height:"100%",width:`${Math.min(weight*100,100)}%`,background:isBase?C.muted2:C.accent,borderRadius:3,transition:"width .4s"}}/>}
      </div>
      <div style={{width:50,textAlign:"right",fontSize:12,fontWeight:600,color:avg?C.text:C.muted3}}>{avg?dollar(avg):"—"}</div>
      <div style={{width:60,fontSize:10,color:C.muted3,textAlign:"right"}}>{count>0?`${count} satış`:avg?"fallback":""}</div>
    </div>
  );
}

function SuggestedCard({ data, label, color, C, cached, cacheAge }) {
  if (!data) return null;
  if (data.error) return <div style={{padding:12,background:C.surface2,borderRadius:8,color:C.red,fontSize:11}}>{label}: {data.error}</div>;

  const p = data.periods || {};
  const hasData = data.suggested != null;
  const isProxy = data.data_source === "browse_proxy";

  return (
    <div style={{background:C.surface2,border:`1px solid ${isProxy?C.orange:data.volatile_warning?C.orange:C.border}`,borderRadius:10,padding:20,flex:1}}>
      {/* Proxy banner — removed (Finding API deprecated) */}
      {/* Başlık + Önerilen fiyat */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
        <div>
          <div style={{fontSize:11,color:C.muted,marginBottom:4,letterSpacing:"0.02em"}}>{label}</div>
          {hasData
            ? <div style={{fontSize:36,fontWeight:700,color,lineHeight:1}}>{dollar(data.suggested)}</div>
            : <div style={{fontSize:20,color:C.muted3,fontWeight:600}}>Veri yok</div>}
          <div style={{fontSize:10,color:C.muted3,marginTop:4}}>
            {isProxy
              ? <span style={{color:"inherit"}}>📊 Aktif eBay listelerinden proxy · <b>SATIŞ fiyatı DEĞİL</b></span>
              : "avg_30d×0.25 + avg_90d×0.25 + avg_365d×0.50 (gerçek satış verisi)"}
          </div>
        </div>
        <div style={{textAlign:"right"}}>
          {cached && (
            <div style={{background:"rgba(96,165,250,.1)",border:`1px solid ${C.blue}`,borderRadius:6,padding:"3px 8px",fontSize:10,color:C.blue,marginBottom:6}}>
              ⚡ cache · {cacheAge ? `${Math.round(cacheAge/60)}dk önce` : ""}
            </div>
          )}
          {isProxy && (
            <div style={{background:"rgba(251,146,60,.12)",border:`1px solid ${C.orange}`,borderRadius:6,padding:"3px 8px",fontSize:10,color:C.orange,marginBottom:6}}>
              Browse proxy
            </div>
          )}
          {data.volatile_warning && (
            <div style={{background:"rgba(251,146,60,.12)",border:`1px solid ${C.orange}`,borderRadius:6,padding:"4px 10px",fontSize:10,color:C.orange,marginBottom:6}}>
              ⚠️ Fiyat tutarsız
            </div>
          )}
          {data.volatility && (
            <div style={{fontSize:11,color:data.volatile_warning?C.orange:C.muted}}>
              volatility {data.volatility}x
            </div>
          )}
          {data.fallback_used && !isProxy && (
            <div style={{fontSize:10,color:C.muted3,marginTop:4}}>3yr fallback kullanıldı</div>
          )}
        </div>
      </div>

      {/* Period breakdown */}
      <div>
        {isProxy ? (
          <>
            <PeriodBar label="En ucuz %40"  avg={p.avg_30d?.avg}  count={p.avg_30d?.count||0}  weight={0.5}  C={C} />
            <PeriodBar label="Ort. (aktif)" avg={p.avg_90d?.avg}  count={p.avg_90d?.count||0}  weight={1.0}  C={C} />
          </>
        ) : (
          <>
            <PeriodBar label="Son 30 gün"  avg={p.avg_30d?.avg}  count={p.avg_30d?.count||0}  weight={0.25} C={C} />
            <PeriodBar label="Son 90 gün"  avg={p.avg_90d?.avg}  count={p.avg_90d?.count||0}  weight={0.25} C={C} />
            <PeriodBar label="Son 1 yıl"   avg={p.avg_365d?.avg} count={p.avg_365d?.count||0} weight={0.50} C={C} />
            <PeriodBar label="3yr fallback" avg={p.avg_3yr?.avg}  count={p.avg_3yr?.count||0}  weight={0.15} C={C} isBase />
          </>
        )}
      </div>
    </div>
  );
}


// ─── Source link builder (used in all result tables) ─────────────────────────
function SourceLinks({ isbn, asin, C, bookTitle="", bookAuthor="" }) {
  const isbn13 = toIsbn13(isbn) || isbn;
  // Resale siteleri için arama terimi: title+author varsa onları kullan, yoksa ISBN
  const titleQuery = bookTitle
    ? encodeURIComponent(`${bookTitle}${bookAuthor ? " " + bookAuthor.split(" ").pop() : ""}`)
    : isbn13;
  const base = [
    {label:"AMZ", title:"Amazon",     url:`https://www.amazon.com/dp/${asin||isbn}`,                        bg:"#FF9900"},
    {label:"eBay",title:"eBay",       url:`https://www.ebay.com/sch/i.html?_nkw=${isbn13}&_sacat=267`,      bg:"#E53238"},
    {label:"ABE", title:"AbeBooks",   url:`https://www.abebooks.com/servlet/SearchResults?isbn=${isbn}`,    bg:"#990000"},
    {label:"TB",  title:"ThriftBooks",url:`https://www.thriftbooks.com/browse/?b.search=${isbn}`,           bg:"#2E7D32"},
    {label:"BF",  title:"BookFinder", url:`https://www.bookfinder.com/search/?isbn=${isbn}&new=1&used=1`,   bg:"#1565C0"},
    {label:"KP",  title:"Keepa",      url:`https://keepa.com/#!product/1-${asin||isbn}`,                   bg:"#7B1FA2"},
  ];
  const resale = [
    // Resale marketplaces — title+author araması (varsa) ISBN'den çok daha iyi sonuç verir
    {label:"MCR",title:"Mercari",     url:`https://www.mercari.com/search/?keyword=${titleQuery}`,          bg:"#FF0211"},
    {label:"DEP",title:"Depop",       url:`https://www.depop.com/search/?q=${titleQuery}`,                  bg:"#FF4040"},
    {label:"PSH",title:"Poshmark",    url:`https://poshmark.com/search?query=${titleQuery}&type=listings`,  bg:"#C2185B"},
    {label:"ETY",title:"Etsy",        url:`https://www.etsy.com/search?q=${titleQuery}`,                   bg:"#F45800"},
    {label:"BPL",title:"BookPal",     url:`https://www.bookpal.com/search?q=${isbn13}`,                    bg:"#0277BD"},
    {label:"BDP",title:"BookDepot",   url:`https://www.bookdepot.com/Store/Search.aspx?q=${isbn13}`,       bg:"#37474F"},
    {label:"TBR",title:"TextbookRush",url:`https://www.textbookrush.com/search?q=${isbn13}`,               bg:"#1B5E20"},
    {label:"CHG",title:"Chegg",       url:`https://www.chegg.com/search?q=${isbn13}`,                      bg:"#E85E00"},
    {label:"VLR",title:"ValoreBooks Sellback",url:`https://www.valore.com/sellback?isbn=${isbn13}`,        bg:"#1A237E"},
  ];
  const A = ({label,title,url,bg,dim}) => (
    <a key={label} href={url} target="_blank" rel="noreferrer" title={title}
      style={{display:"inline-block",padding:"2px 5px",borderRadius:3,fontSize:9,fontWeight:700,
        background:bg,color:"#fff",textDecoration:"none",marginRight:2,opacity:dim?0.7:1}}>
      {label}
    </a>
  );
  return (
    <span style={{whiteSpace:"nowrap"}}>
      {base.map(l=><A key={l.label} {...l}/>)}
      {resale.map(l=><A key={l.label} {...l} dim/>)}
    </span>
  );
}

function PricingTab({ isbns, C, push, titles, rules, onRulesSaved }) {
  const [selected, setSelected] = useState(isbns[0]||"");
  const [goodLimit, setGoodLimit] = useState(30);
  const [newLimit, setNewLimit] = useState(50);
  const [suggestedResult, setSuggestedResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [backoff, setBackoff] = useState(null);
  const [isbnNewMax, setIsbnNewMax] = useState("");
  const [isbnUsedMax, setIsbnUsedMax] = useState("");
  const [isbnInterval, setIsbnInterval] = useState("");
  const [savingRule, setSavingRule] = useState(false);
  const [activeStats, setActiveStats] = useState(null);   // /ebay/active-stats result
  const [statsLoading, setStatsLoading] = useState(false);
  const [showStatsModal, setShowStatsModal] = useState(false);

  // Populate override fields when ISBN changes
  useEffect(() => {
    if (selected && rules[selected]) {
      setIsbnNewMax(rules[selected].new_max != null ? String(rules[selected].new_max) : "");
      setIsbnUsedMax(rules[selected].used_all_max != null ? String(rules[selected].used_all_max) : "");
      const secs = rules[selected].interval_seconds;
      setIsbnInterval(secs ? fmtSecs(secs) : "");
    } else {
      setIsbnNewMax(""); setIsbnUsedMax(""); setIsbnInterval("");
    }
  }, [selected, rules]);

  const saveIsbnRule = async () => {
    if (!selected) return;
    setSavingRule(true);
    try {
      if (isbnNewMax || isbnUsedMax) {
        await req(`/rules/${selected}/override`, {method:"PUT", body:JSON.stringify({
          new_max: isbnNewMax ? Number(isbnNewMax) : undefined,
          used_all_max: isbnUsedMax ? Number(isbnUsedMax) : undefined,
        })});
      }
      const secs = isbnInterval ? parseSecs(isbnInterval) : null;
      if (secs) {
        await req(`/rules/${selected}/interval`, {method:"PUT", body:JSON.stringify({interval_seconds:secs})});
      }
      push(`${selected} kuralları güncellendi`, "success");
      if (onRulesSaved) onRulesSaved();
    } catch(e) { push("Kayıt hatası: "+e.message, "error"); }
    finally { setSavingRule(false); }
  };

  /* Finding API backoff — deprecated, removed from UI */

  const limits = {
    new: newLimit, like_new: Math.round(goodLimit*1.15), very_good: Math.round(goodLimit*1.10),
    good: goodLimit, acceptable: Math.round(goodLimit*0.80), offer: Math.round(goodLimit*1.30),
  };

  const condRows = [
    {label:"Brand New",color:C.green,limit:limits.new},
    {label:"Like New",color:C.blue,limit:limits.like_new},
    {label:"Very Good",color:C.purple,limit:limits.very_good},
    {label:"Good",color:C.accent,limit:limits.good},
    {label:"Acceptable",color:C.orange,limit:limits.acceptable},
  ];

  const fetchSuggested = async (forceRefresh = false) => {
    if (!selected) return;
    setLoading(true);
    try {
      const url = `/suggested-price/${selected}${forceRefresh ? "?force_refresh=true" : ""}`;
      const res = await req(url, {}, 60000);
      setSuggestedResult(res);
      push(res.cached ? `Cache'den döndü (${Math.round(res.cache_age_seconds/60)}dk önce)` : "Sorgulama tamamlandı", "success");
    } catch(e) {
      push("Hata: " + e.message, "error");
    } finally { setLoading(false); }
  };

  const fetchActiveStats = async () => {
    if (!selected) return;
    setStatsLoading(true);
    try {
      const res = await req(`/ebay/active-stats/${selected}`, {}, 30000);
      setActiveStats(res);
    } catch(e) {
      push("Active stats hatası: " + e.message, "error");
    } finally { setStatsLoading(false); }
  };

  // Auto-fetch active stats when ISBN changes
  useEffect(() => { setActiveStats(null); if (selected) fetchActiveStats(); }, [selected]);

  return (
    <div>
      {/* Limit Tablosu */}
      <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:24,marginBottom:20}}>
        <ST C={C} style={{marginBottom:16}}>Fiyat Limitleri (USD)</ST>
        <div style={{display:"flex",gap:20,flexWrap:"wrap",marginBottom:20}}>
          {[{label:"Good (Baz)",val:goodLimit,set:setGoodLimit,color:C.accent},{label:"Brand New",val:newLimit,set:setNewLimit,color:C.green}].map(r=>(
            <div key={r.label}>
              <div style={{fontSize:10,color:C.muted,marginBottom:6}}>{r.label}</div>
              <input type="number" value={r.val} onChange={e=>r.set(Number(e.target.value))} className="inp" style={{width:90,textAlign:"right",color:r.color}}/>
            </div>
          ))}
        </div>
        <div style={{display:"grid",gridTemplateColumns:"repeat(5,1fr)",gap:10}}>
          {condRows.map(r=>(
            <div key={r.label} style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:8,padding:"12px 14px",position:"relative",overflow:"hidden"}}>
              <div style={{position:"absolute",top:0,left:0,right:0,height:2,background:r.color}}/>
              <div style={{fontSize:10,color:C.muted,marginBottom:4}}>{r.label}</div>
              <div style={{fontSize:22,fontWeight:600,color:r.color}}>{dollar(r.limit)}</div>
            </div>
          ))}
        </div>
        <div style={{marginTop:12,display:"flex",justifyContent:"space-between",padding:"8px 0",borderTop:`1px solid ${C.border}`}}>
          <span style={{fontSize:11,color:C.muted}}>Make Offer tavan</span>
          <span style={{fontSize:14,fontWeight:600,color:C.blue}}>{dollar(limits.offer)} <span style={{fontSize:10,color:C.muted3}}>(×1.30)</span></span>
        </div>
      </div>

      {/* Per-ISBN Kural Override */}
      {selected && (
        <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:20,marginBottom:20}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:14}}>
            <ST C={C} style={{marginBottom:0}}>ISBN Kural Override — {selected}{titles[selected]?` · ${titles[selected]}`:""}</ST>
            <span style={{fontSize:10,color:C.muted3}}>Boş bırakırsan varsayılan</span>
          </div>
          <div style={{display:"flex",gap:12,flexWrap:"wrap",alignItems:"flex-end"}}>
            <div>
              <div style={{fontSize:10,color:C.muted,marginBottom:4}}>New Max ($)</div>
              <input className="inp" type="number" placeholder="örn: 50" value={isbnNewMax} onChange={e=>setIsbnNewMax(e.target.value)} style={{width:100,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.green}}/>
            </div>
            <div>
              <div style={{fontSize:10,color:C.muted,marginBottom:4}}>Used Good Max ($)</div>
              <input className="inp" type="number" placeholder="örn: 30" value={isbnUsedMax} onChange={e=>setIsbnUsedMax(e.target.value)} style={{width:100,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.accent}}/>
            </div>
            <div>
              <div style={{fontSize:10,color:C.muted,marginBottom:4}}>Interval</div>
              <input className="inp" placeholder="4h / 30m / 1d" value={isbnInterval} onChange={e=>setIsbnInterval(e.target.value)} style={{width:100,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.blue}}/>
            </div>
            {isbnUsedMax && (
              <div style={{fontSize:10,color:C.muted3,lineHeight:1.9}}>
                Like New: <b style={{color:C.blue}}>${Math.round(Number(isbnUsedMax)*1.15)}</b>{" "}
                VG: <b style={{color:C.purple}}>${Math.round(Number(isbnUsedMax)*1.10)}</b>{" "}
                Accept: <b style={{color:C.orange}}>${Math.round(Number(isbnUsedMax)*0.80)}</b>
              </div>
            )}
            <button className="add-btn" onClick={saveIsbnRule} disabled={savingRule} style={{padding:"8px 20px"}}>
              {savingRule ? "Kaydediliyor…" : "✓ Kaydet"}
            </button>
          </div>
        </div>
      )}

      {/* Active Stats Modal */}
      {showStatsModal && activeStats && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.65)",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",padding:16}}
          onClick={e=>{if(e.target===e.currentTarget)setShowStatsModal(false);}}>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:14,padding:28,width:560,maxWidth:"100%",maxHeight:"90vh",overflowY:"auto",boxShadow:"0 24px 60px rgba(0,0,0,.5)"}}
            onKeyDown={e=>e.key==="Escape"&&setShowStatsModal(false)}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:18}}>
              <div>
                <div style={{fontSize:14,fontWeight:600,color:C.text}}>Aktif Listeler — {activeStats.isbn}</div>
                <div style={{fontSize:10,color:C.muted3,marginTop:2}}>eBay Browse · anlık, FIXED_PRICE</div>
              </div>
              <div style={{display:"flex",gap:8,alignItems:"center"}}>
                <button
                  onClick={async()=>{
                    await reportBrokenLink({ isbn: activeStats.isbn, url: buildEbaySearchUrl({ isbn: activeStats.isbn }), context: "pricing_modal" });
                    alert("Teşekkürler — link sorunu kaydedildi.");
                  }}
                  title="eBay linklerinde sorun mu var? Bildir."
                  style={{background:"none",border:`1px solid ${C.border}`,borderRadius:5,color:C.muted3,fontFamily:"var(--mono)",fontSize:10,padding:"4px 10px",cursor:"pointer"}}
                >
                  🔗 Link bozuk?
                </button>
                <button onClick={()=>setShowStatsModal(false)} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:5,color:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"4px 12px",cursor:"pointer"}}>✕ Kapat</button>
              </div>
            </div>

            {/* Overall new vs used */}
            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:12,marginBottom:20}}>
              {activeStats.overall?.new && (
                <div style={{background:C.surface2,border:`1px solid ${C.green}`,borderRadius:8,padding:"12px 16px"}}>
                  <div style={{fontSize:10,color:C.green,marginBottom:6,fontWeight:600}}>BRAND NEW</div>
                  <div style={{fontSize:20,fontWeight:700,color:C.green}}>${activeStats.overall.new.min} <span style={{fontSize:12,fontWeight:400,color:C.muted}}>min</span></div>
                  <div style={{fontSize:12,color:C.muted,marginTop:2}}>{activeStats.overall.new.count} ilan · ort ${activeStats.overall.new.avg}</div>
                </div>
              )}
              {activeStats.overall?.used && (
                <div style={{background:C.surface2,border:`1px solid ${C.accent}`,borderRadius:8,padding:"12px 16px"}}>
                  <div style={{fontSize:10,color:C.accent,marginBottom:6,fontWeight:600}}>USED (tüm)</div>
                  <div style={{fontSize:20,fontWeight:700,color:C.accent}}>${activeStats.overall.used.min} <span style={{fontSize:12,fontWeight:400,color:C.muted}}>min</span></div>
                  <div style={{fontSize:12,color:C.muted,marginTop:2}}>{activeStats.overall.used.count} ilan · ort ${activeStats.overall.used.avg}</div>
                </div>
              )}
            </div>

            {/* By condition table */}
            {Object.keys(activeStats.by_condition||{}).length > 0 && (
              <div style={{marginBottom:20}}>
                <div style={{fontSize:10,color:C.muted,marginBottom:8,letterSpacing:"0.08em",fontWeight:500}}>Kondisyon Kırılımı</div>
                <table style={{width:"100%",borderCollapse:"collapse",fontSize:12}}>
                  <thead>
                    <tr style={{borderBottom:`1px solid ${C.border}`}}>
                      {["Kondisyon","Adet","Min","Ort",""].map(h=>(
                        <th key={h} style={{textAlign:h==="Kondisyon"?"left":"right",padding:"4px 8px",color:C.muted,fontSize:10,fontWeight:500}}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[["brand_new","New",C.green],["like_new","Like New",C.blue],["very_good","Very Good",C.purple],["good","Good",C.accent],["acceptable","Acceptable",C.orange],["used_all","Used",C.muted]].map(([k,label,color])=>{
                      const st = activeStats.by_condition?.[k];
                      if (!st) return null;
                      return (
                        <tr key={k} style={{borderBottom:`1px solid ${C.border}20`}}>
                          <td style={{padding:"7px 8px"}}>
                            <span style={{color,fontWeight:500}}>{label}</span>
                          </td>
                          <td style={{padding:"7px 8px",textAlign:"right",color:C.muted}}>{st.count}</td>
                          <td style={{padding:"7px 8px",textAlign:"right",color:C.text,fontWeight:600}}>${st.min}</td>
                          <td style={{padding:"7px 8px",textAlign:"right",color:C.muted}}>${st.avg}</td>
                          <td style={{padding:"7px 8px",textAlign:"right"}}>
                            <a href={buildEbaySearchUrl({ isbn: activeStats.isbn, condition: k })} target="_blank" rel="noreferrer"
                              style={{fontSize:10,color:C.accent,border:`1px solid ${C.accent}`,borderRadius:3,padding:"1px 7px",textDecoration:"none"}}>
                              eBay ↗
                            </a>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}

            {/* Top 10 cheapest */}
            {activeStats.top_cheapest?.length > 0 && (
              <div>
                <div style={{fontSize:10,color:C.muted,marginBottom:8,letterSpacing:"0.08em",fontWeight:500}}>En Ucuz 10 İlan</div>
                {activeStats.top_cheapest.map((it,i)=>{
                  const condColors = {brand_new:C.green,like_new:C.blue,very_good:C.purple,good:C.accent,acceptable:C.orange,used_all:C.muted};
                  const condLabels = {brand_new:"New",like_new:"Like New",very_good:"Very Good",good:"Good",acceptable:"Acceptable",used_all:"Used"};
                  return (
                    <div key={it.itemId||i} style={{display:"flex",alignItems:"center",gap:10,padding:"7px 0",borderBottom:`1px solid ${C.border}20`}}>
                      <a href={it.url||"#"} target="_blank" rel="noreferrer" style={{flexShrink:0,display:"block"}}>
                        {it.image
                          ? <img src={it.image} loading="lazy" width={36} height={36} style={{borderRadius:4,objectFit:"cover",background:C.surface2}} alt=""/>
                          : <div style={{width:36,height:36,borderRadius:4,background:C.surface2}}/>
                        }
                      </a>
                      <div style={{flex:1,minWidth:0,fontSize:11,color:C.muted,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{it.title}</div>
                      <span style={{color:condColors[it.bucket]||C.muted,fontSize:10,flexShrink:0}}>{condLabels[it.bucket]||it.bucket}</span>
                      <span style={{color:C.text,fontWeight:600,fontSize:13,flexShrink:0}}>${it.total}</span>
                      <a href={buildEbaySearchUrl({ isbn: activeStats.isbn, condition: it.bucket })} target="_blank" rel="noreferrer"
                        title={`eBay'de ${condLabels[it.bucket]||it.bucket} kondisyonlu, en ucuzdan`}
                        style={{flexShrink:0,fontSize:10,color:C.accent,border:`1px solid ${C.accent}`,borderRadius:3,padding:"1px 7px",textDecoration:"none",whiteSpace:"nowrap"}}>
                        eBay ↗
                      </a>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Önerilen Fiyat Sorgulama */}
      <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:24}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}>
          <ST C={C} style={{marginBottom:0}}>Önerilen Alım Fiyatı</ST>
          <div style={{display:"flex",gap:8,alignItems:"center"}}>
            {activeStats && !statsLoading && (
              <button onClick={()=>setShowStatsModal(true)} style={{background:"none",border:`1px solid ${C.blue}`,borderRadius:5,color:C.blue,fontFamily:"var(--mono)",fontSize:11,padding:"4px 12px",cursor:"pointer"}}>
                📊 Aktif {activeStats.overall?.used ? `· ${activeStats.overall.used.count+( activeStats.overall.new?.count||0)} ilan` : ""}
              </button>
            )}
            {statsLoading && <span style={{fontSize:10,color:C.muted3}}>⟳ aktif…</span>}
          </div>
        </div>
        {/* Active stats inline summary */}
        {activeStats && !statsLoading && (
          <div style={{display:"flex",gap:16,flexWrap:"wrap",marginBottom:16,padding:"10px 14px",background:C.surface2,borderRadius:8,border:`1px solid ${C.border}`}}>
            {activeStats.overall?.new && (
              <span style={{fontSize:11,color:C.muted}}>
                New: <b style={{color:C.green}}>{activeStats.overall.new.count}</b> ilan
                {" · "}min <b style={{color:C.green}}>${activeStats.overall.new.min}</b>
                {" · "}avg <b style={{color:C.muted}}>${activeStats.overall.new.avg}</b>
              </span>
            )}
            {activeStats.overall?.used && (
              <span style={{fontSize:11,color:C.muted}}>
                Used: <b style={{color:C.accent}}>{activeStats.overall.used.count}</b> ilan
                {" · "}min <b style={{color:C.accent}}>${activeStats.overall.used.min}</b>
                {" · "}avg <b style={{color:C.muted}}>${activeStats.overall.used.avg}</b>
              </span>
            )}
            <button onClick={()=>setShowStatsModal(true)} style={{background:"none",border:"none",color:C.muted3,fontFamily:"var(--mono)",fontSize:10,cursor:"pointer",padding:0,textDecoration:"underline"}}>
              detay →
            </button>
          </div>
        )}

        <div style={{display:"flex",gap:10,alignItems:"center",marginBottom:24}}>
          <select className="inp" value={selected} onChange={e=>setSelected(e.target.value)} style={{flex:1,maxWidth:300}}>
            {isbns.length===0 ? <option value="">Önce watchlist'e ISBN ekle</option> : isbns.map(isbn=><option key={isbn} value={isbn}>{isbn}{titles[isbn] ? ` — ${titles[isbn]}` : ""}</option>)}
          </select>
          <button className="add-btn" onClick={()=>fetchSuggested(false)} disabled={loading||!selected}>
            {loading ? (
              <span style={{display:"flex",alignItems:"center",gap:6}}>
                <span style={{display:"inline-block",animation:"spin 1s linear infinite"}}>⟳</span> Hesaplanıyor…
              </span>
            ) : "📊 Hesapla"}
          </button>
          {suggestedResult && !loading && (
            <button onClick={()=>fetchSuggested(true)} disabled={loading} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:"pointer"}} title="Cache'i atla, fresh veri çek">
              ↻ Yenile
            </button>
          )}
        </div>

        {loading && (
          <div style={{padding:"24px",textAlign:"center",color:C.muted,fontSize:12}}>
            eBay'den 4 dönem verisi çekiliyor (30g / 100g / 1y / 3y)…<br/>
            <span style={{fontSize:10,color:C.muted3,marginTop:4,display:"block"}}>Bu işlem ~10 saniye sürebilir</span>
          </div>
        )}

        {suggestedResult && !loading && (
          <div>
            <div style={{fontSize:11,color:C.muted,marginBottom:12}}>ISBN: {suggestedResult.isbn}</div>
            <div style={{display:"flex",gap:16,flexWrap:"wrap"}}>
              <SuggestedCard data={suggestedResult.used} label="Kullanılmış" color={C.accent} C={C} cached={suggestedResult.cached} cacheAge={suggestedResult.cache_age_seconds}/>
              <SuggestedCard data={suggestedResult.new}  label="Yeni"        color={C.green}  C={C} cached={suggestedResult.cached} cacheAge={suggestedResult.cache_age_seconds}/>
            </div>

            {/* Limit karşılaştırması */}
            {(suggestedResult.used?.suggested || suggestedResult.new?.suggested) && (
              <div style={{marginTop:16,padding:"14px 16px",background:C.surface2,borderRadius:8,border:`1px solid ${C.border}`}}>
                <ST C={C} style={{marginBottom:10}}>Limit Karşılaştırması</ST>
                <div style={{display:"grid",gridTemplateColumns:"repeat(2,1fr)",gap:10}}>
                  {suggestedResult.used?.suggested && (
                    <div style={{fontSize:12}}>
                      <span style={{color:C.muted}}>Kullanılmış önerilen: </span>
                      <span style={{color:C.accent,fontWeight:600}}>{dollar(suggestedResult.used.suggested)}</span>
                      <span style={{color:C.muted3,fontSize:10}}> vs Good limit {dollar(goodLimit)}</span>
                      <span style={{marginLeft:8,color:suggestedResult.used.suggested<=goodLimit?C.green:C.red,fontSize:11}}>
                        {suggestedResult.used.suggested<=goodLimit?"✓ limit dahilinde":"↑ limit üstünde"}
                      </span>
                    </div>
                  )}
                  {suggestedResult.new?.suggested && (
                    <div style={{fontSize:12}}>
                      <span style={{color:C.muted}}>Yeni önerilen: </span>
                      <span style={{color:C.green,fontWeight:600}}>{dollar(suggestedResult.new.suggested)}</span>
                      <span style={{color:C.muted3,fontSize:10}}> vs New limit {dollar(newLimit)}</span>
                      <span style={{marginLeft:8,color:suggestedResult.new.suggested<=newLimit?C.green:C.red,fontSize:11}}>
                        {suggestedResult.new.suggested<=newLimit?"✓ limit dahilinde":"↑ limit üstünde"}
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {!suggestedResult && !loading && (
          <div style={{border:`1px dashed ${C.border}`,borderRadius:8,padding:32,textAlign:"center",color:C.muted3,fontSize:12}}>
            ISBN seçip hesapla butonuna bas<br/>
            <span style={{fontSize:10,marginTop:4,display:"block"}}>New ve used kondisyon ayrı ayrı hesaplanır</span>
          </div>
        )}
      </div>
    </div>
  );
}




// ─── Deal Score Badge ─────────────────────────────────────────────────────────
// score null → eski entry, gösterme
// ─── Score ring: büyük, net, tek bakışta ─────────────────────────────────────
function ScoreRing({ score, C }) {
  if (score == null) return <div style={{width:44,height:44}}/>;
  const tier = score >= 75 ? "fire" : score >= 50 ? "good" : "low";
  const color = tier === "fire" ? C.green : tier === "good" ? C.accent : C.muted2;
  const emoji = tier === "fire" ? "🔥" : tier === "good" ? "✨" : null;
  return (
    <div title={`Deal Score ${score}/100`} style={{
      width:44, height:44, borderRadius:"50%",
      border:`2px solid ${color}`,
      display:"flex", flexDirection:"column",
      alignItems:"center", justifyContent:"center",
      flexShrink:0, gap:0,
      background: tier === "fire" ? "rgba(52,211,153,.08)" : tier === "good" ? "rgba(240,165,0,.07)" : "transparent",
    }}>
      {emoji && <span style={{fontSize:10,lineHeight:1}}>{emoji}</span>}
      <span style={{fontSize:13,fontWeight:700,color,lineHeight:1}}>{score}</span>
    </div>
  );
}

// Thumbnail with eBay → OpenLibrary fallback, safe against infinite onError loops
function Thumb({ imageUrl, isbn, href, C, size = 72 }) {
  const olCover = `https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg`;
  const [src, setSrc] = useState(imageUrl || olCover);
  const [triedOl, setTriedOl] = useState(!imageUrl);
  return (
    <a href={href || "#"} target="_blank" rel="noreferrer"
      onClick={e => e.stopPropagation()}
      style={{display:"flex",alignItems:"stretch",width:size,flexShrink:0}}>
      <div style={{
        width:size, minHeight:size, flexShrink:0,
        background:C.surface2,
        display:"flex", alignItems:"center", justifyContent:"center",
        overflow:"hidden",
      }}>
        <img
          src={src}
          loading="lazy"
          style={{width:"100%",height:"100%",objectFit:"contain",display:"block"}}
          onError={() => { if (!triedOl) { setTriedOl(true); setSrc(olCover); }}}
          alt=""
        />
      </div>
    </a>
  );
}


// ═══════════════════════════════════════════════════════════════════
// DISCOVER TAB — CSV Arbitrage Scanner
// ═══════════════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════════════
// SCAN HISTORY TAB
// ═══════════════════════════════════════════════════════════════════
function ScanHistoryTab({ C, addCandidate, candidates=[] }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [verifyResults, setVerifyResults] = useState({});
  const [verifying, setVerifying] = useState(new Set());
  const [verifyDrawer, setVerifyDrawer] = useState(null);

  const verifyOne = async (key, row) => {
    setVerifying(prev => new Set([...prev, key]));
    try {
      const res = await fetch("/verify/listing", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({isbn: row.isbn, candidate: row}),
      });
      const data = await res.json();
      setVerifyResults(prev => ({...prev, [key]: data}));
    } catch(e) {
      setVerifyResults(prev => ({...prev, [key]: {status:"ERROR", summary: e.message}}));
    } finally {
      setVerifying(prev => { const s=new Set(prev); s.delete(key); return s; });
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const d = await req("/discover/history", {}, 10000);
      if (d.ok) setHistory(d.history || []);
    } catch(e) {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const fmtDate = ts => new Date(ts*1000).toLocaleString("tr-TR", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
  const tierColor = t => t==="fire"?"#f97316":t==="good"?"#22c55e":t==="low"?"#3b82f6":"#ef4444";

  const exportCsv = (entry) => {
    const cols = ["isbn","asin","source","source_condition","buy_price","amazon_sell_price","match_type","profit","roi_pct","roi_tier"];
    const header = cols.join(",");
    const lines = (entry.accepted||[]).map(r => cols.map(c => r[c]??'').join(","));
    const blob = new Blob([header+"\n"+lines.join("\n")], {type:"text/csv"});
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = `scan_${new Date(entry.ts*1000).toISOString().slice(0,10)}_${entry.job_id}.csv`; a.click();
  };

  if (loading) return <div style={{textAlign:"center",paddingTop:80,color:C.muted3}}>Yükleniyor…</div>;
  if (!history.length) return (
    <div style={{textAlign:"center",paddingTop:80,color:C.muted3}}>
      <div style={{fontSize:32,marginBottom:12}}>📋</div>
      <div>Henüz kayıtlı tarama yok</div>
      <div style={{fontSize:11,marginTop:6}}>Discover sekmesinden tarama yap</div>
    </div>
  );

  return (
    <>
    <div style={{display:"flex",gap:20}}>
      {/* List */}
      <div style={{width:280,flexShrink:0}}>
        <div style={{fontSize:12,fontWeight:600,color:C.text,marginBottom:10}}>
          📋 Geçmiş Taramalar ({history.length})
        </div>
        {history.map((entry,i) => (
          <div key={entry.job_id}
            onClick={()=>setSelected(entry)}
            style={{background: selected?.job_id===entry.job_id ? C.surface2 : C.surface,
              border:`1px solid ${selected?.job_id===entry.job_id ? C.accent : C.border}`,
              borderRadius:8, padding:"10px 12px", marginBottom:6, cursor:"pointer"}}>
            <div style={{fontSize:10,color:C.muted,marginBottom:3}}>{fmtDate(entry.ts)}</div>
            <div style={{display:"flex",gap:8,alignItems:"center"}}>
              <span style={{fontSize:13,fontWeight:600,color:C.green}}>✅ {entry.stats?.accepted_count||0}</span>
              <span style={{fontSize:11,color:C.muted}}>/ {entry.stats?.total_isbns||"?"} ISBN</span>
            </div>
            {entry.stats?.duration_s && <div style={{fontSize:10,color:C.muted3,marginTop:2}}>⏱ {entry.stats.duration_s}s</div>}
          </div>
        ))}
      </div>

      {/* Detail */}
      <div style={{flex:1,minWidth:0}}>
        {!selected ? (
          <div style={{textAlign:"center",paddingTop:80,color:C.muted3}}>Sol taraftan bir tarama seç</div>
        ) : (
          <>
            <div style={{display:"flex",gap:10,marginBottom:14,flexWrap:"wrap",alignItems:"center"}}>
              {[
                {label:"Toplam ISBN", val:selected.stats?.total_isbns||"?", color:C.text},
                {label:"✅ Kabul", val:selected.stats?.accepted_count||0, color:C.green},
                {label:"❌ Elenen", val:selected.rejected_count||0, color:C.muted},
                {label:"⏱ Süre", val:(selected.stats?.duration_s||"?")+"s", color:C.blue},
                {label:"⚠️ Amazon yok", val:selected.stats?.amazon_unavailable||0, color:"#f97316"},
              ].map(s=>(
                <div key={s.label} style={{background:C.surface,border:`1px solid ${C.border}`,
                  borderRadius:8,padding:"8px 12px",minWidth:80}}>
                  <div style={{fontSize:10,color:C.muted}}>{s.label}</div>
                  <div style={{fontSize:16,fontWeight:700,color:s.color}}>{s.val}</div>
                </div>
              ))}
              <button onClick={()=>exportCsv(selected)} style={{marginLeft:"auto",padding:"6px 12px",
                fontSize:11,borderRadius:6,cursor:"pointer",background:C.surface2,
                color:C.muted,border:`1px solid ${C.border}`}}>⬇ CSV İndir</button>
            </div>

            {/* Top reject reasons */}
            {selected.top_reasons && Object.keys(selected.top_reasons).length > 0 && (
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,
                padding:"10px 14px",marginBottom:14}}>
                <div style={{fontSize:11,fontWeight:600,color:C.text,marginBottom:8}}>Eleme Sebepleri</div>
                <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                  {Object.entries(selected.top_reasons).map(([reason,count])=>(
                    <span key={reason} style={{padding:"3px 8px",borderRadius:4,fontSize:10,
                      background:C.surface2,color:C.muted}}>
                      {reason}: <b style={{color:C.text}}>{count}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Results table */}
            {(selected.accepted||[]).length === 0 ? (
              <div style={{padding:40,textAlign:"center",color:C.muted3,fontSize:12}}>
                Bu taramada kabul edilen sonuç yok
              </div>
            ) : (
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,overflow:"hidden"}}>
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                    <thead>
                      <tr style={{background:C.surface2,borderBottom:`1px solid ${C.border}`}}>
                        {["ISBN","ASIN","Kaynak","Cond","Alım $","Amazon $","Eşleşme","Kar $","ROI %","Tier","Linkler","Doğrula",""].map(h=>(
                          <th key={h} style={{padding:"8px 10px",textAlign:"left",color:C.muted,fontWeight:600,whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(selected.accepted||[]).map((r,i)=>(
                        <tr key={i} style={{borderBottom:`1px solid ${C.border}`,background:i%2===0?C.surface:C.surface2}}>
                          <td style={{padding:"7px 10px",color:C.text,fontFamily:"monospace"}}>{r.isbn}</td>
                          <td style={{padding:"7px 10px",color:C.muted,fontFamily:"monospace",fontSize:10}}>{r.asin||"—"}</td>
                          <td style={{padding:"7px 10px",color:C.accent}}>{r.source}</td>
                          <td style={{padding:"7px 10px"}}>
                            <span style={{padding:"2px 6px",borderRadius:4,fontSize:10,fontWeight:600,
                              background:r.source_condition==="new"?`${C.green}22`:`${C.accent}22`,
                              color:r.source_condition==="new"?C.green:C.accent}}>
                              {(r.source_condition||"").toUpperCase()}
                            </span>
                          </td>
                          <td style={{padding:"7px 10px",color:C.text}}>{r.buy_price>0?`$${r.buy_price}`:"—"}</td>
                          <td style={{padding:"7px 10px",color:C.text}}>{r.amazon_sell_price!=null?`$${r.amazon_sell_price}`:"—"}</td>
                          <td style={{padding:"7px 10px",color:C.muted,fontSize:10}}>{r.match_type||"—"}</td>
                          <td style={{padding:"7px 10px",fontWeight:600,color:r.profit>0?C.green:"#ef4444"}}>{r.profit!=null?`$${r.profit}`:"—"}</td>
                          <td style={{padding:"7px 10px",fontWeight:600,color:tierColor(r.roi_tier)}}>{r.roi_pct!=null?`${r.roi_pct}%`:"—"}</td>
                          <td style={{padding:"7px 10px"}}>
                            {r.roi_tier&&<span style={{padding:"2px 6px",borderRadius:4,fontSize:10,fontWeight:700,
                              background:`${tierColor(r.roi_tier)}22`,color:tierColor(r.roi_tier)}}>
                              {r.roi_tier==="fire"?"🔥":r.roi_tier==="good"?"✅":r.roi_tier==="low"?"🔵":"❌"}
                            </span>}
                          </td>
                          <td style={{padding:"6px 8px"}}>
                            <SourceLinks isbn={r.isbn} asin={r.asin} C={C}
                              bookTitle={r.google_title||r.ebay_title||""}
                              bookAuthor={(r.edition_authors||[]).join(" ")||""}
                            />
                          </td>
                          <td style={{padding:"6px 8px",textAlign:"center",minWidth:90}}>
                            {verifying.has(i) ? (
                              <span style={{fontSize:10,color:C.muted}}>⏳</span>
                            ) : verifyResults[i] ? (
                              <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:2}}>
                                <span style={{fontSize:9,fontWeight:700,
                                  color:{VERIFIED:"#22c55e",VERIFIED_STOCK_PHOTO:"#f97316",GONE:"#ef4444",PRICE_UP:"#f97316",PRICE_DOWN:"#3b82f6",MISMATCH:"#ef4444",UNVERIFIABLE:"#64748b"}[verifyResults[i].status]||"#94a3b8",
                                  cursor:"pointer"}} onClick={()=>verifyOne(i,r)}>
                                  {{VERIFIED:"✅",VERIFIED_STOCK_PHOTO:"📷⚠️",GONE:"💀",PRICE_UP:"📈",PRICE_DOWN:"📉",MISMATCH:"⚠️",UNVERIFIABLE:"ℹ️"}[verifyResults[i].status]||"?"} {verifyResults[i].status}
                                </span>
                                <button onClick={()=>setVerifyDrawer({rowIdx:i,row:r})}
                                  style={{padding:"1px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                                    background:"#a855f711",color:"#a855f7",border:"1px solid #a855f744",fontFamily:"var(--mono)"}}>
                                  🧠 Detay
                                </button>
                              </div>
                            ) : (
                              <button onClick={()=>verifyOne(i,r)}
                                style={{padding:"2px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                                  background:"#0ea5e911",color:"#0ea5e9",border:"1px solid #0ea5e944",fontFamily:"var(--mono)"}}>
                                🔍 Doğrula
                              </button>
                            )}
                          </td>
                          <td style={{padding:"6px 8px",textAlign:"center"}}>
                            {(()=>{
                              const inCand = candidates.some(cx=>cx.isbn===r.isbn&&cx.source===r.source&&cx.source_condition===r.source_condition);
                              return <button onClick={()=>!inCand&&addCandidate&&addCandidate(r)}
                                title={inCand?"Zaten aday listesinde":"Aday listesine ekle"}
                                style={{background:inCand?"#f59e0b33":"transparent",
                                  border:`1px solid ${inCand?"#f59e0b":"#f59e0b55"}`,
                                  borderRadius:5,padding:"3px 7px",cursor:inCand?"default":"pointer",
                                  fontSize:13,opacity:inCand?1:0.6}}>
                                {inCand?"⭐":"☆"}
                              </button>;
                            })()}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>

    {verifyDrawer && verifyResults[verifyDrawer.rowIdx] && (
      <VerifyDetailDrawer C={C} data={verifyResults[verifyDrawer.rowIdx]}
        row={verifyDrawer.row} onClose={()=>setVerifyDrawer(null)}/>
    )}
  </>
  );
}


// ── Rejection reason → okunabilir Türkçe ──────────────────────────────────
const fmtReason = (raw) => {
  if (!raw) return "—";
  const LABELS = {
    "no_ebay_listings":          "eBay'de ilan yok",
    "amazon_unavailable":        "Amazon fiyatı alınamadı",
    "missing_used_buybox":       "Amazon used buybox yok",
    "missing_new_buybox":        "Amazon new buybox yok",
    "roi_below":                 "ROI yetersiz",
    "profit_below":              "Kâr yetersiz",
    "buy_price_above":           "Alım fiyatı çok yüksek",
    "amazon_price_below":        "Amazon fiyatı çok düşük",
    "amazon_price_above":        "Amazon fiyatı çok yüksek",
    "buy_ratio_above":           "Alım/satış oranı yüksek",
    "invalid_isbn_or_not_978":   "Geçersiz ISBN",
    "ebay_token_error":          "eBay token hatası",
    "ebay_not_configured":       "eBay ayarlanmamış",
    "no_viable_condition_match": "Kondisyon eşleşmesi yok",
  };
  // shipping_unknown — eBay'de ilan VAR ama kargo CALCULATED
  if (raw.startsWith("shipping_unknown:")) return "⚠️ Kargo bilinmiyor — ilan var ama fiyat alınamadı";
  // policy_filtered
  if (raw.startsWith("policy_filtered(recall)")) return "⚠️ Kargo bilinmiyor (Recall modunda)";
  const policyMatch = raw.match(/^policy_filtered\(([^)]+)\):(.+)$/);
  if (policyMatch) return "Eşleşme yok (" + policyMatch[1] + ") — Recall dene";
  if (raw.startsWith("no_valid_offers:")) return "Geçerli ilan yok";
  // ebay_error:... → kısa
  if (raw.startsWith("ebay_error:")) return "eBay hatası";
  // invalid_isbn:... → kısa
  if (raw.startsWith("invalid_isbn:")) return "Geçersiz ISBN";
  // roi_below_X → ROI < X%
  const roiM = raw.match(/roi_below_([\d.]+)/);
  if (roiM) return `ROI < %${roiM[1]}`;
  // profit_below_X
  const profM = raw.match(/profit_below_([\d.]+)/);
  if (profM) return `Kâr < $${profM[1]}`;
  // buy_price_above_X
  const buyM = raw.match(/buy_price_above_([\d.]+)/);
  if (buyM) return `Alım > $${buyM[1]}`;
  // amazon_price_below_X
  const amzLM = raw.match(/amazon_price_below_([\d.]+)/);
  if (amzLM) return `Amazon < $${amzLM[1]}`;
  // Direct label lookup
  if (LABELS[raw]) return LABELS[raw];
  // Fallback: underscores → spaces, capitalize
  return raw.replace(/_/g," ").replace(/^./,s=>s.toUpperCase());
};
function DiscoverTab({ C, theme, scanJob, setScanJob, scanPollRef, candidates=[], addCandidate, removeCandidate, saveCandidates, push, isbns: watchlistIsbns=[], addIsbn }) {
  const [csvText, setCsvText] = useState("");
  const [fileName, setFileName] = useState("");
  const [isbnBuyPrices, setIsbnBuyPrices] = useState({}); // {isbn: buyPrice} — CSV'den gelen opsiyonel fiyatlar
  const [isbnAmazonPrices, setIsbnAmazonPrices] = useState({}); // {isbn: avgPrice} — Amazon Business Report ortalaması
  const [csvReportType, setCsvReportType] = useState(""); // "amazon_business_report" | "generic"
  const [csvTitleMap, setCsvTitleMap] = useState({}); // {isbn: title}
  // Scan state App seviyesinde — tab degisince kaybolmaz
  const jobId    = scanJob?.jobId    ?? null;
  const progress = scanJob?.progress ?? null;
  const results  = scanJob?.results  ?? null;
  const scanning = scanJob?.scanning ?? false;
  const setJobId    = v => setScanJob(p => ({...(p||{}), jobId: typeof v==="function"?v(p?.jobId):v}));
  const setProgress = v => setScanJob(p => ({...(p||{}), progress: typeof v==="function"?v(p?.progress):v}));
  const setResults  = v => setScanJob(p => ({...(p||{}), results: typeof v==="function"?v(p?.results):v}));
  const setScanning = v => setScanJob(p => ({...(p||{}), scanning: typeof v==="function"?v(p?.scanning):v}));
  const pollRef = scanPollRef;
  const [error, setError] = useState("");
  const [activeView, setActiveView] = useState("accepted");
  const [selectedRows, setSelectedRows] = useState(new Set()); // indices of selected accepted rows
  const toggleRow = (i) => setSelectedRows(prev => { const s=new Set(prev); s.has(i)?s.delete(i):s.add(i); return s; });
  const selectAll = () => setSelectedRows(new Set((results?.accepted||[]).map((_,i)=>i)));
  const deselectAll = () => setSelectedRows(new Set());
  const isCandidate = (r) => candidates.some(c=>c.isbn===r.isbn&&c.source===r.source&&c.source_condition===r.source_condition);
  const addSelected = () => {
    const rows = results?.accepted||[];
    selectedRows.forEach(i=>{ if(rows[i]) addCandidate(rows[i]); });
    setSelectedRows(new Set());
  };

  // Filters
  const [strictMode, setStrictMode] = useState(true);
  const [onlyViable, setOnlyViable] = useState(true);
  const [isbnMatchPolicy, setIsbnMatchPolicy] = useState("recall");
  const [invalidIsbnPolicy, setInvalidIsbnPolicy] = useState("best_effort");
  const [verifiedOnlyFilter, setVerifiedOnlyFilter] = useState(false);
  const [buybackOnlyFilter, setBuybackOnlyFilter] = useState(false);
  const [minBuybackProfit, setMinBuybackProfit] = useState("");
  const [verifyResults, setVerifyResults] = useState({}); // {rowIndex: {status, summary, ...}}
  const [verifying, setVerifying] = useState(new Set()); // indices being verified
  const [bulkVerifying, setBulkVerifying] = useState(false);
  const [verifyDrawer, setVerifyDrawer] = useState(null); // {rowIdx, row} — detail panel open
  const [discoverSubTab, setDiscoverSubTab] = useState("scan"); // "scan" | "results" | "candidates"
  const [minRoi, setMinRoi] = useState("");
  const [maxRoi, setMaxRoi] = useState("");
  const [minProfit, setMinProfit] = useState("");
  const [minAmazon, setMinAmazon] = useState("");
  const [maxAmazon, setMaxAmazon] = useState("");
  const [maxBuyRatio, setMaxBuyRatio] = useState(""); // % of amazon price, e.g. 50 = max 50% of buybox
  const [minBuy, setMinBuy] = useState("");
  const [maxBuy, setMaxBuy] = useState("");
  const [condFilter, setCondFilter] = useState("all"); // "all"|"new"|"used"
  const [sourceFilter, setSourceFilter] = useState("all"); // "all"|"ebay"|"thriftbooks"|"abebooks"
  const [concurrency, setConcurrency] = useState(1);

  // Dynamic limit calculator
  const [calcSell, setCalcSell] = useState("");
  const [calcRoi, setCalcRoi] = useState("30");
  const [calcResult, setCalcResult] = useState(null);

  const fileRef = useRef();

  // ── File upload handler ──────────────────────────────────────────

  // ── RFC 4180 uyumlu CSV parser ─────────────────────────────────────
  // BOM temizleme, quoted fields, multi-delimiter algılama
  const _parseCsvRobust = (text) => {
    // BOM temizle
    const clean = text.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    // Delimiter algıla: tab > semicolon > comma
    const firstLine = clean.split("\n")[0] || "";
    const delim = firstLine.includes("\t") ? "\t" : firstLine.includes(";") ? ";" : ",";
    const rows = [];
    let row = [], field = "", inQuote = false;
    for (let i = 0; i < clean.length; i++) {
      const ch = clean[i];
      if (inQuote) {
        if (ch === '"' && clean[i+1] === '"') { field += '"'; i++; }
        else if (ch === '"') { inQuote = false; }
        else { field += ch; }
      } else {
        if (ch === '"') { inQuote = true; }
        else if (ch === delim) { row.push(field.trim()); field = ""; }
        else if (ch === "\n") { row.push(field.trim()); rows.push(row); row = []; field = ""; }
        else { field += ch; }
      }
    }
    if (field || row.length) { row.push(field.trim()); if (row.some(Boolean)) rows.push(row); }
    return rows;
  };

  // CSV/XLSX'ten ISBN/ASIN + opsiyonel fiyat kolonunu parse et
  // Amazon Business Report formatını otomatik algılar
  const _parseRows = (rows) => {
    const parseNum = v => { const n = parseFloat(String(v||"").replace(/[^0-9.]/g,"")); return isFinite(n)&&n>0 ? n : null; };
    const isIsbn  = v => { const s = String(v||"").replace(/[^0-9X]/gi,"").trim(); return (s.length===10||s.length===13) ? s : null; };

    const firstRow = rows[0] || [];
    const headers = firstRow.map(h => String(h||"").toLowerCase().trim());

    // ── Amazon Business Report tespiti ─────────────────────────────
    // Kolonlar: [1]=(Child) ASIN, [14]=Units Ordered, [18]=Ordered Product Sales
    const isAmazonReport = headers.some(h => h.includes("ordered product sales"))
                        && headers.some(h => h.includes("child") && h.includes("asin"));
    if (isAmazonReport) {
      const asinCol   = headers.findIndex(h => h.includes("child") && h.includes("asin"));
      const unitsCol  = headers.findIndex(h => h === "units ordered");
      const salesCol  = headers.findIndex(h => h === "ordered product sales");
      const titleCol  = headers.findIndex(h => h === "title");
      const isbns = [], priceMap = {}, titleMap = {};
      for (const row of rows.slice(1)) {
        const asin = isIsbn(row[asinCol]);
        if (!asin) continue;
        const units = parseInt(row[unitsCol]||"0");
        const sales = parseNum(row[salesCol]);
        const avg   = (units > 0 && sales > 0) ? Math.round(sales/units*100)/100 : null;
        const title = titleCol >= 0 ? String(row[titleCol]||"").slice(0,60) : "";
        if (!isbns.includes(asin)) {
          isbns.push(asin);
          if (avg) priceMap[asin] = avg;
          if (title) titleMap[asin] = title;
        }
      }
      return { isbns, priceMap, titleMap, reportType: "amazon_business_report" };
    }

    // ── Genel CSV formatı ────────────────────────────────────────────
    const isbnCol  = headers.findIndex(h => h.includes("isbn") || h.includes("ean") || h.includes("asin"));
    const priceCol = headers.findIndex(h => h.includes("buy") || h.includes("price") || h.includes("fiyat") || h.includes("alım") || h.includes("cost"));
    const hasHeader = isbnCol >= 0;
    const dataRows = hasHeader ? rows.slice(1) : rows;
    const isbns = [], priceMap = {};

    for (const row of dataRows) {
      if (!row||!row.length) continue;
      let isbn = null, price = null;
      if (hasHeader) {
        isbn  = isIsbn(row[isbnCol]);
        if (priceCol >= 0) price = parseNum(row[priceCol]);
      } else {
        for (let i = 0; i < row.length; i++) {
          const c = isIsbn(row[i]);
          if (c) { isbn = c; price = parseNum(row[i+1]); break; }
        }
      }
      if (isbn && !isbns.includes(isbn)) {
        isbns.push(isbn);
        if (price) priceMap[isbn] = price;
      }
    }
    return { isbns, priceMap, titleMap: {}, reportType: "generic" };
  };

  const handleFile = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setFileName(file.name);
    const ext = file.name.split(".").pop().toLowerCase();

    if (ext === "csv" || ext === "txt") {
      const text = await file.text();
      const rows = _parseCsvRobust(text);
      const { isbns, priceMap, titleMap, reportType } = _parseRows(rows);
      setCsvText(isbns.join("\n"));
      setCsvReportType(reportType||"");
      setCsvTitleMap(titleMap||{});
      if (reportType === "amazon_business_report") {
        setIsbnAmazonPrices(priceMap); setIsbnBuyPrices({});
      } else {
        setIsbnBuyPrices(priceMap); setIsbnAmazonPrices({});
      }
      setError("");
    } else if (ext === "xlsx" || ext === "xls") {
      try {
        const XLSX = await import("https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js");
        const buf = await file.arrayBuffer();
        const wb = XLSX.read(buf, { type: "array" });
        const ws = wb.Sheets[wb.SheetNames[0]];
        const rows = XLSX.utils.sheet_to_json(ws, { header: 1 });
        const { isbns, priceMap, titleMap, reportType } = _parseRows(rows);
        setCsvText(isbns.join("\n"));
        setCsvReportType(reportType||"");
        setCsvTitleMap(titleMap||{});
        if (reportType === "amazon_business_report") {
          setIsbnAmazonPrices(priceMap); setIsbnBuyPrices({});
        } else {
          setIsbnBuyPrices(priceMap); setIsbnAmazonPrices({});
        }
      } catch(err) {
        setError("XLSX okunamadı: " + err.message);
      }
    } else {
      setError("Desteklenen formatlar: .csv .txt .xlsx .xls");
    }
  };

  const isbns = csvText.split("\n").map(s => s.trim()).filter(Boolean);

  // ── Scan (background job + polling) ────────────────────────────
  const stopPolling = () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };

  // Remount'ta aktif job varsa polling'i yeniden başlat
  useEffect(() => {
    const jid = scanJob?.jobId;
    if (!jid || scanJob?.progress?.status === "done" || scanJob?.progress?.status === "error") return;
    if (pollRef.current) return; // zaten çalışıyor
    pollRef.current = setInterval(async () => {
      try {
        const pd = await req("/discover/csv-arb/progress/" + jid, {}, 10000);
        if (!pd.ok) return;
        // progress güncelle
        setScanJob(p => ({...(p||{}), progress: {done:pd.progress, total:pd.total, eta_s:pd.eta_s,
          status:pd.status, accepted_count:pd.accepted_count, rejected_count:pd.rejected_count}}));
        // Tarama devam ederken partial results'ı HEMEN göster
        if (pd.accepted && pd.accepted.length > 0) {
          setScanJob(p => ({...(p||{}),
            results: {
              ok: true,
              accepted: pd.accepted,
              rejected: pd.rejected || [],
              partial: pd.status !== "done",
              stats: pd.stats || {}
            }
          }));
        }
        if (pd.status === "done") {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          // Final sonuçlar için result endpoint'i çek (tam liste)
          const rd = await req("/discover/csv-arb/result/" + jid, {}, 30000);
          if (rd.ok) setScanJob(p => ({...(p||{}), results: rd, scanning: false}));
          else setScanJob(p => ({...(p||{}), scanning: false}));
        } else if (pd.status === "error") {
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
          // Hata olsa bile partial results kalsın
          setScanJob(p => ({...(p||{}), scanning: false}));
        }
      } catch(e) {}
    }, 1500);
    return; // cleanup YOK — interval App-level ref'te yaşıyor
  }, []); // sadece mount'ta çalış

  const runScan = async () => {
    if (!isbns.length) return;
    stopPolling();
    setScanning(true); setResults(null); setError(""); setJobId(null);
    setProgress({done:0, total:isbns.length, eta_s:null, status:"pending"});

    const body = {
      isbns,
      strict_mode: strictMode,
      only_viable: onlyViable,
      concurrency,
      ...(Object.keys(isbnBuyPrices).length ? {isbn_buy_prices: isbnBuyPrices} : {}),
      ...(Object.keys(isbnAmazonPrices).length ? {isbn_amazon_prices: isbnAmazonPrices} : {}),
      ...(minRoi   ? {min_roi_pct: parseFloat(minRoi)}   : {}),
      ...(maxRoi   ? {max_roi_pct: parseFloat(maxRoi)}   : {}),
      ...(minProfit? {min_profit_usd: parseFloat(minProfit)}: {}),
      ...(buybackOnlyFilter ? {buyback_only: true} : {}),
      ...(minBuybackProfit ? {min_buyback_profit: parseFloat(minBuybackProfit)} : {}),
      ...(minAmazon? {min_amazon_price: parseFloat(minAmazon)}: {}),
      ...(maxAmazon? {max_amazon_price: parseFloat(maxAmazon)}: {}),
      ...(minBuy   ? {min_buy_price: parseFloat(minBuy)} : {}),
      ...(maxBuy   ? {max_buy_price: parseFloat(maxBuy)} : {}),
      ...(condFilter !== "all" ? {condition_in: [condFilter]} : {}),
      ...(sourceFilter !== "all" ? {source_in: [sourceFilter]} : {}),
      ...(maxBuyRatio ? {max_buy_ratio_pct: parseFloat(maxBuyRatio)} : {}),
      isbn_match_policy: isbnMatchPolicy,
      invalid_isbn_policy: invalidIsbnPolicy,
    };

    try {
      // 1. Job başlat — hemen job_id döner
      const data = await req("/discover/csv-arb", {method:"POST", body:JSON.stringify(body)}, 30000);
      if (!data.ok) {
        if (data.queued) {
          setError(`⏳ Başka bir tarama devam ediyor (Job: ${data.active_job_id} — ${data.active_job_progress}). Bitmesini bekle.`);
        } else {
          setError(data.message || data.detail || "Başlatılamadı");
        }
        setScanning(false); return;
      }

      const jid = data.job_id;
      setJobId(jid);
      setProgress({done:0, total:data.total, eta_s:data.estimated_seconds, status:"running"});

      // 2. Poll progress
      pollRef.current = setInterval(async () => {
        try {
          const pd = await req("/discover/csv-arb/progress/" + jid, {}, 10000);
          if (!pd.ok) return;
          setProgress({done:pd.progress, total:pd.total, eta_s:pd.eta_s, status:pd.status,
            accepted_count:pd.accepted_count, rejected_count:pd.rejected_count});

          if (pd.status === "done") {
            stopPolling();
            // Tam sonucu çek
            const rd = await req("/discover/csv-arb/result/" + jid, {}, 30000);
            if (rd.ok) setResults(rd);
            else setError("Sonuç alınamadı");
            setScanning(false);
          } else if (pd.status === "error") {
            stopPolling();
            setError("Tarama hatası: " + (pd.error || "bilinmiyor"));
            setScanning(false);
          }
        } catch(e) { /* poll hatası — devam et */ }
      }, 1500);

    } catch(e) {
      setError("Bağlantı hatası: " + e.message);
      setScanning(false);
    }
  };

  // ── Dynamic limit calc ───────────────────────────────────────────
  const calcMaxBuy = async () => {
    if (!calcSell) return;
    try {
      const data = await req("/discover/suggest-max-buy", {
        method: "POST",
        body: JSON.stringify({sell_price: parseFloat(calcSell), target_roi_pct: parseFloat(calcRoi)||30}),
      }, 10000);
      setCalcResult(data);
    } catch(e) {
      setCalcResult({ok: false, reason: e.message});
    }
  };

  // ── Export CSV ───────────────────────────────────────────────────
  // ─── Listing verify ────────────────────────────────────────────────────────
  const verifyOne = async (rowIdx, row) => {
    setVerifying(prev => new Set([...prev, rowIdx]));
    try {
      const res = await fetch("/verify/listing", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({isbn: row.isbn, candidate: row}),
      });
      const data = await res.json();
      setVerifyResults(prev => ({...prev, [rowIdx]: data}));
    } catch(e) {
      setVerifyResults(prev => ({...prev, [rowIdx]: {status: "ERROR", summary: e.message}}));
    } finally {
      setVerifying(prev => { const s=new Set(prev); s.delete(rowIdx); return s; });
    }
  };

  const verifySelected = async () => {
    const rows = results?.accepted || [];
    const toVerify = selectedRows.size > 0
      ? [...selectedRows].map(i => ({_index: i, isbn: rows[i]?.isbn, candidate: rows[i]}))
      : rows.slice(0, 20).map((r,i) => ({_index: i, isbn: r.isbn, candidate: r}));
    if (!toVerify.length) return;
    setBulkVerifying(true);
    try {
      const res = await fetch("/verify/batch", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({items: toVerify, concurrency: 4}),
      });
      const data = await res.json();
      const newResults = {};
      (data.results || []).forEach(r => { if(r._index != null) newResults[r._index] = r; });
      setVerifyResults(prev => ({...prev, ...newResults}));
    } catch(e) { console.error("Bulk verify error:", e); }
    finally { setBulkVerifying(false); }
  };

  const verifyStatusColor = (status) => {
    const m = {VERIFIED:"#22c55e", VERIFIED_STOCK_PHOTO:"#f97316",
                GONE:"#ef4444", PRICE_UP:"#f97316", UNVERIFIABLE:"#64748b",
                PRICE_DOWN:"#3b82f6", MISMATCH:"#ef4444", ERROR:"#6b7280"};
    return m[status] || "#6b7280";
  };

  const verifyStatusEmoji = (status) => {
    const m = {VERIFIED:"✅", VERIFIED_STOCK_PHOTO:"📷⚠️", UNVERIFIABLE:"ℹ️",
                GONE:"💀", PRICE_UP:"📈", PRICE_DOWN:"📉", MISMATCH:"⚠️", ERROR:"❓"};
    return m[status] || "❓";
  };

  // Vision verdict badge
  const visionBadge = (vr) => {
    if (!vr || !vr.verdict || vr.verdict === "NO_IMAGE" || vr.status === "SKIP") return null;
    const map = {
      MATCH:        {col:"#22c55e", bg:"#22c55e18", icon:"📷✓", label:"Kapak eşleşiyor"},
      MISMATCH:     {col:"#ef4444", bg:"#ef444418", icon:"📷✗", label:"FARKLI KİTAP"},
      UNCERTAIN:    {col:"#f97316", bg:"#f9731618", icon:"📷?", label:"Belirsiz"},
      STOCK_PHOTO:  {col:"#f97316", bg:"#f9731618", icon:"📷🏭", label:"Stock fotoğraf"},
    };
    const s = map[vr.verdict] || {col:"#6b7280", bg:"#6b728018", icon:"📷", label:vr.verdict};
    return (
      <span title={vr.notes || ""} style={{
        display:"inline-block", marginLeft:4, fontSize:9, padding:"1px 4px",
        borderRadius:3, background:s.bg, color:s.col, fontWeight:700, cursor:"help"
      }}>
        {s.icon} {s.label}
        {vr.confidence ? ` ${vr.confidence}%` : ""}
      </span>
    );
  };

  const exportCsv = () => {
    if (!results) return;
    const rows = activeView === "accepted" ? results.accepted : results.rejected;
    const cols = ["isbn","asin","source","source_condition","buy_price","amazon_sell_price",
                  "buybox_type","match_type","profit","roi_pct","roi_tier","reason"];
    const header = cols.join(",");
    const lines = rows.map(r => cols.map(c => {
      const v = r[c] ?? "";
      return typeof v === "string" && v.includes(",") ? `"${v}"` : v;
    }).join(","));
    const blob = new Blob([header + "\n" + lines.join("\n")], {type:"text/csv"});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `arb_${activeView}_${Date.now()}.csv`;
    a.click();
  };

  const inpStyle = {background:C.inputBg, border:`1px solid ${C.inputBorder}`, color:C.text,
    borderRadius:6, padding:"5px 8px", fontSize:11, width:"100%"};
  const labelStyle = {fontSize:10, color:C.muted, marginBottom:2, display:"block"};
  const tierColor = (tier) => tier==="fire"?"#f97316":tier==="good"?C.green:tier==="low"?C.blue:C.red||"#ef4444";

  // Auto-switch to results when scan completes
  const prevScanningRef = useRef(false);
  useEffect(() => {
    if (prevScanningRef.current && !scanJob?.scanning && scanJob?.results) {
      setDiscoverSubTab("results");
    }
    prevScanningRef.current = scanJob?.scanning || false;
  }, [scanJob?.scanning, scanJob?.results]);

  const subTabStyle = (id) => ({
    padding:"7px 18px", fontSize:11, borderRadius:"6px 6px 0 0", cursor:"pointer",
    fontFamily:"var(--mono)", fontWeight: discoverSubTab===id ? 600 : 400,
    background: discoverSubTab===id ? C.surface : "transparent",
    color: discoverSubTab===id ? C.accent : C.muted,
    border: discoverSubTab===id ? `1px solid ${C.border}` : "1px solid transparent",
    borderBottom: discoverSubTab===id ? `1px solid ${C.surface}` : `1px solid ${C.border}`,
    transition:"all .15s", marginBottom:-1,
  });

  return (
    <>
    {/* Sub-tab navigation */}
    <div style={{display:"flex", gap:2, marginBottom:0, borderBottom:`1px solid ${C.border}`}}>
      <button style={subTabStyle("scan")} onClick={()=>setDiscoverSubTab("scan")}>
        🔍 Tara
      </button>
      <button style={subTabStyle("results")} onClick={()=>setDiscoverSubTab("results")}>
        📊 Sonuçlar {results ? `(${(results.accepted||[]).length})` : ""}
      </button>
      <button style={subTabStyle("candidates")} onClick={()=>setDiscoverSubTab("candidates")}>
        ⭐ Adaylar {candidates.length>0?`(${candidates.length})`:""}
      </button>
    </div>

    {/* Sub-tab: Scan */}
    {discoverSubTab==="scan" && (

    <div style={{display:"flex", gap:20, alignItems:"flex-start", paddingTop:16}}>
      {/* Left panel */}
      <div style={{width:300, flexShrink:0}}>

        {/* Upload */}
        <div style={{background:C.surface, border:`1px solid ${C.border}`, borderRadius:10, padding:16, marginBottom:12}}>
          <div style={{fontSize:12, fontWeight:600, color:C.text, marginBottom:10}}>📂 ISBN Dosyası</div>
          <div
            onClick={() => fileRef.current?.click()}
            style={{border:`2px dashed ${C.border2}`, borderRadius:8, padding:"20px 10px",
              textAlign:"center", cursor:"pointer", color:C.muted, fontSize:11, marginBottom:8,
              background: fileName ? C.surface2 : "transparent"}}
          >
            {fileName ? `✅ ${fileName}` : "CSV / XLSX / TXT yükle"}
          </div>
          <input ref={fileRef} type="file" accept=".csv,.xlsx,.xls,.txt"
            onChange={handleFile} style={{display:"none"}}/>
          <textarea
            value={csvText}
            onChange={e=>setCsvText(e.target.value)}
            placeholder={"ISBN'leri buraya yapıştır (her satıra bir tane)\n9780132350884\n9781234567890\n..."}
            rows={6}
            style={{...inpStyle, width:"100%", resize:"vertical", fontFamily:"monospace", marginTop:4}}
          />
          <div style={{fontSize:10, color:C.muted3, marginTop:4}}>
            {isbns.length} ISBN/ASIN · max 500
            {csvReportType === "amazon_business_report" && (
              <span style={{marginLeft:8, color:C.accent}}>📊 Amazon Business Report</span>
            )}
          </div>
          {csvReportType === "amazon_business_report" && Object.keys(isbnAmazonPrices).length > 0 && (
            <div style={{marginTop:6, background:C.surface2, borderRadius:6, padding:"6px 8px", fontSize:10}}>
              <div style={{color:C.accent, fontWeight:600, marginBottom:4}}>
                📊 Amazon Business Report — {Object.keys(isbnAmazonPrices).length} ürün
              </div>
              <div style={{color:C.muted, marginBottom:3, fontSize:9}}>
                Ortalama satış fiyatı (Ordered Product Sales ÷ Units Ordered)
              </div>
              {Object.entries(isbnAmazonPrices).slice(0,3).map(([asin, price]) => (
                <div key={asin} style={{color:C.text, fontFamily:"monospace", marginBottom:1}}>
                  {asin}
                  {csvTitleMap[asin] && <span style={{color:C.muted, fontFamily:"sans-serif"}}> — {csvTitleMap[asin].slice(0,30)}</span>}
                  <span style={{color:C.green}}> ≈${price}</span>
                </div>
              ))}
              {Object.keys(isbnAmazonPrices).length > 3 && (
                <div style={{color:C.muted}}>+{Object.keys(isbnAmazonPrices).length-3} daha…</div>
              )}
              <button onClick={()=>{setIsbnAmazonPrices({}); setCsvReportType(""); setCsvTitleMap({});}}
                style={{marginTop:4, fontSize:10, color:C.red||"#ef4444", background:"none",
                  border:"none", cursor:"pointer", padding:0}}>
                ✕ Sıfırla
              </button>
            </div>
          )}
          {csvReportType !== "amazon_business_report" && Object.keys(isbnBuyPrices).length > 0 && (
            <div style={{marginTop:6, background:C.surface2, borderRadius:6, padding:"6px 8px", fontSize:10}}>
              <div style={{color:C.muted, marginBottom:3}}>📋 Alım fiyatı önizleme (ilk 3):</div>
              {Object.entries(isbnBuyPrices).slice(0,3).map(([isbn, price]) => (
                <div key={isbn} style={{color:C.text, fontFamily:"monospace"}}>
                  {isbn} → <span style={{color:C.green}}>${price}</span>
                </div>
              ))}
              {Object.keys(isbnBuyPrices).length > 3 && (
                <div style={{color:C.muted}}>+{Object.keys(isbnBuyPrices).length-3} daha…</div>
              )}
              <button onClick={()=>setIsbnBuyPrices({})}
                style={{marginTop:4, fontSize:10, color:C.red||"#ef4444", background:"none",
                  border:"none", cursor:"pointer", padding:0}}>
                ✕ Fiyatları sıfırla
              </button>
            </div>
          )}
        </div>

        {/* Filters */}
        <div style={{background:C.surface, border:`1px solid ${C.border}`, borderRadius:10, padding:16, marginBottom:12}}>
          <div style={{fontSize:12, fontWeight:600, color:C.text, marginBottom:10}}>⚙️ Filtreler</div>

          {/* Strict mode */}
          <label style={{display:"flex", alignItems:"center", gap:6, fontSize:11, color:C.text, marginBottom:4, cursor:"pointer"}}>
            <input type="checkbox" checked={strictMode} onChange={e=>setStrictMode(e.target.checked)}/>
            <span>Strict Mode <span style={{fontSize:9,color:C.muted}}>(NEW/Like New→AMZ New · Used→AMZ Used)</span></span>
          </label>
          <label style={{display:"flex", alignItems:"center", gap:6, fontSize:11, color:C.text, marginBottom:8, cursor:"pointer"}}>
            <input type="checkbox" checked={onlyViable} onChange={e=>setOnlyViable(e.target.checked)}/>
            <span>Sadece Kârlı <span style={{fontSize:9,color:C.muted}}>(profit &gt; 0)</span></span>
          </label>

          {/* ISBN Match Policy */}
          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:6, marginBottom:8}}>
            <div>
              <span style={{fontSize:9,color:C.muted,display:"block",marginBottom:2}}>ISBN Eşleşme Politikası</span>
              <select value={isbnMatchPolicy} onChange={e=>setIsbnMatchPolicy(e.target.value)}
                style={{width:"100%",fontSize:11,padding:"4px 6px",borderRadius:5,border:`1px solid ${C.border}`,background:C.surface2,color:C.text}}>
                <option value="recall">🕸 Recall (önerilen — hepsini gör)</option>
                <option value="balanced">⚖️ Balanced (sadece GTIN eşleşmesi)</option>
                <option value="precision">🎯 Precision (kesin eşleşme zorunlu)</option>
              </select>
            </div>
            <div>
              <span style={{fontSize:9,color:C.muted,display:"block",marginBottom:2}}>Geçersiz ISBN</span>
              <select value={invalidIsbnPolicy} onChange={e=>setInvalidIsbnPolicy(e.target.value)}
                style={{width:"100%",fontSize:11,padding:"4px 6px",borderRadius:5,border:`1px solid ${C.border}`,background:C.surface2,color:C.text}}>
                <option value="best_effort">🔍 Best Effort (dene, işaretle)</option>
                <option value="reject">🚫 Reject (reddet)</option>
              </select>
            </div>
          </div>

          {/* Verified only filter */}
          <label style={{display:"flex", alignItems:"center", gap:6, fontSize:11, color:C.text, marginBottom:4, cursor:"pointer"}}>
            <input type="checkbox" checked={verifiedOnlyFilter} onChange={e=>setVerifiedOnlyFilter(e.target.checked)}/>
            <span>Sadece Doğrulanmış <span style={{fontSize:9,color:C.muted}}>(CONFIRMED eşleşmeler)</span></span>
          </label>

          {/* Buyback only filter */}
          <label style={{display:"flex", alignItems:"center", gap:6, fontSize:11, color:C.text, marginBottom:4, cursor:"pointer"}}>
            <input type="checkbox" checked={buybackOnlyFilter} onChange={e=>setBuybackOnlyFilter(e.target.checked)}/>
            <span>💰 Buyback kârlı <span style={{fontSize:9,color:C.muted}}>(profit &gt; $0)</span></span>
          </label>
          <div style={{marginBottom:8}}>
            <span style={labelStyle}>Min Buyback Kâr $</span>
            <input style={inpStyle} type="number" value={minBuybackProfit}
              onChange={e=>setMinBuybackProfit(e.target.value)}
              placeholder="örn: 5"/>
          </div>

          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:6, marginBottom:6}}>
            <div>
              <span style={labelStyle}>Min ROI %</span>
              <input style={inpStyle} type="number" value={minRoi} onChange={e=>setMinRoi(e.target.value)} placeholder="örn: 20"/>
            </div>
            <div>
              <span style={labelStyle}>Max ROI %</span>
              <input style={inpStyle} type="number" value={maxRoi} onChange={e=>setMaxRoi(e.target.value)} placeholder="opsiyonel"/>
            </div>
            <div>
              <span style={labelStyle}>Min Kar $</span>
              <input style={inpStyle} type="number" value={minProfit} onChange={e=>setMinProfit(e.target.value)} placeholder="örn: 2"/>
            </div>
            <div>
              <span style={labelStyle}>Min Alım $</span>
              <input style={inpStyle} type="number" value={minBuy} onChange={e=>setMinBuy(e.target.value)} placeholder="opsiyonel"/>
            </div>
            <div>
              <span style={labelStyle}>Max Alım $</span>
              <input style={inpStyle} type="number" value={maxBuy} onChange={e=>setMaxBuy(e.target.value)} placeholder="örn: 15"/>
            </div>
            <div>
              <span style={labelStyle}>Min Amazon Buybox $</span>
              <input style={inpStyle} type="number" value={minAmazon} onChange={e=>setMinAmazon(e.target.value)} placeholder="opsiyonel"/>
            </div>
            <div>
              <span style={labelStyle}>Max Amazon Buybox $</span>
              <input style={inpStyle} type="number" value={maxAmazon} onChange={e=>setMaxAmazon(e.target.value)} placeholder="opsiyonel"/>
            </div>
            <div style={{gridColumn:"1/-1"}}>
              <span style={labelStyle}>⚡ Max Alım (Amazon %'si)</span>
              <div style={{display:"flex",alignItems:"center",gap:4}}>
                <input style={{...inpStyle,width:60}} type="number" value={maxBuyRatio} onChange={e=>setMaxBuyRatio(e.target.value)} placeholder="50"/>
                <span style={{fontSize:11,color:C.muted}}>% → Amazon $100 ise max $50</span>
              </div>
            </div>
          </div>

          <div style={{marginBottom:6}}>
            <span style={labelStyle}>Kondisyon</span>
            <select style={inpStyle} value={condFilter} onChange={e=>setCondFilter(e.target.value)}>
              <option value="all">Tümü (New + Used)</option>
              <option value="new">Sadece New</option>
              <option value="used">Sadece Used</option>
            </select>
          </div>
          <div style={{marginBottom:6}}>
            <span style={labelStyle}>Kaynak</span>
            <select style={inpStyle} value={sourceFilter} onChange={e=>setSourceFilter(e.target.value)}>
              <option value="all">Tümü</option>
              <optgroup label="— eBay">
                <option value="ebay">eBay</option>
              </optgroup>
              <optgroup label="— Kitap Siteleri">
                <option value="thriftbooks">ThriftBooks</option>
                <option value="abebooks">AbeBooks</option>
                <option value="betterworldbooks">BetterWorldBooks</option>
                <option value="biblio">Biblio</option>
                <option value="alibris">Alibris</option>
                <option value="goodwill">GoodwillBooks</option>
                <option value="hpb">HPB (Half Price Books)</option>
              </optgroup>
              <optgroup label="— Toptan / Bulk">
                <option value="bookpal">BookPal</option>
                <option value="bookdepot">BookDepot</option>
                <option value="textbookrush">TextbookRush</option>
                <option value="campusbooks">CampusBooks</option>
                <option value="chegg">Chegg</option>
              </optgroup>
              <optgroup label="— Resale">
                <option value="mercari">Mercari</option>
                <option value="depop">Depop</option>
                <option value="poshmark">Poshmark</option>
                <option value="etsy">Etsy</option>
              </optgroup>
            </select>
          </div>
          <div>
            <span style={labelStyle}>Paralel tarama (1-8)</span>
            <input style={inpStyle} type="number" min={1} max={5} value={concurrency} onChange={e=>setConcurrency(Math.min(5,Math.max(1,parseInt(e.target.value)||1)))}/>
          </div>
        </div>

        {/* Dynamic limit calc */}
        <div style={{background:C.surface, border:`1px solid ${C.border}`, borderRadius:10, padding:16, marginBottom:12}}>
          <div style={{fontSize:12, fontWeight:600, color:C.text, marginBottom:8}}>🧮 Dinamik Limit Hesabı</div>
          <div style={{fontSize:10, color:C.muted, marginBottom:8}}>Hedef ROI için max eBay alım fiyatı</div>
          <div style={{display:"grid", gridTemplateColumns:"1fr 1fr", gap:6, marginBottom:8}}>
            <div>
              <span style={labelStyle}>Amazon satış $</span>
              <input style={inpStyle} type="number" value={calcSell} onChange={e=>setCalcSell(e.target.value)} placeholder="örn: 20"/>
            </div>
            <div>
              <span style={labelStyle}>Hedef ROI %</span>
              <input style={inpStyle} type="number" value={calcRoi} onChange={e=>setCalcRoi(e.target.value)} placeholder="30"/>
            </div>
          </div>
          <button onClick={calcMaxBuy} disabled={!calcSell}
            style={{width:"100%", padding:"7px 0", background:C.accent, color:"#fff",
              border:"none", borderRadius:6, cursor:"pointer", fontSize:11}}>
            Hesapla
          </button>
          {calcResult && (
            <div style={{marginTop:8, fontSize:11}}>
              {calcResult.ok ? (
                <div style={{background:C.surface2, borderRadius:6, padding:"8px 10px"}}>
                  <div style={{color:C.green, fontWeight:700, fontSize:14}}>
                    Max: ${calcResult.max_buy_price}
                  </div>
                  <div style={{color:C.muted, fontSize:10, marginTop:2}}>
                    Amazon: ${calcResult.sell_price} · Fees: ${calcResult.total_fees} · Net: ${calcResult.net_after_fees}
                  </div>
                </div>
              ) : (
                <div style={{color:C.red||"#ef4444", fontSize:10}}>{calcResult.reason}</div>
              )}
            </div>
          )}
        </div>

        {/* Scan button / Pause+Stop */}
        {!scanning ? (
          <button
            onClick={runScan}
            disabled={!isbns.length}
            style={{width:"100%", padding:"12px 0", fontSize:13, fontWeight:700,
              background: !isbns.length ? C.border : C.accent,
              color: !isbns.length ? C.muted : "#fff",
              border:"none", borderRadius:8, cursor: !isbns.length?"not-allowed":"pointer"}}
          >
            🔍 {isbns.length} ISBN Tara
          </button>
        ) : (
          <div style={{display:"flex", flexDirection:"column", gap:8}}>
            {/* Progress bar */}
            <div style={{background:C.surface2, borderRadius:8, padding:"10px 14px",
              border:`1px solid ${scanJob?.paused ? "#f97316" : C.accent}55`}}>
              <div style={{display:"flex", justifyContent:"space-between", marginBottom:6, alignItems:"center"}}>
                <span style={{fontSize:12, fontWeight:700, color: scanJob?.paused ? "#f97316" : C.accent}}>
                  {scanJob?.paused ? "⏸ Duraklatıldı" : "⏳ Taranıyor..."}
                </span>
                <span style={{fontSize:11, color:C.muted}}>
                  {(() => {
                    const done = progress?.done||0, total = progress?.total||0;
                    const pct = total > 0 ? Math.round(done/total*100) : 0;
                    const eta = progress?.eta_s;
                    const etaStr = eta && !scanJob?.paused ? (eta>60 ? ` · ~${Math.ceil(eta/60)}dk` : ` · ~${eta}s`) : "";
                    const accepted = progress?.accepted_count||0;
                    return `${done}/${total} (${pct}%) · ✅ ${accepted} fırsat${etaStr}`;
                  })()}
                </span>
              </div>
              <div style={{height:6, background:C.border, borderRadius:3, overflow:"hidden"}}>
                <div style={{
                  height:"100%", borderRadius:3,
                  background: scanJob?.paused ? "#f97316" : C.accent,
                  width:`${progress?.total > 0 ? Math.round((progress?.done||0)/progress.total*100) : 0}%`,
                  transition:"width 0.4s ease"
                }}/>
              </div>
            </div>
            {/* Pause + Stop */}
            <div style={{display:"flex", gap:8}}>
              <button
                onClick={async () => {
                  if (!jobId) return;
                  if (scanJob?.paused) {
                    await fetch("/discover/csv-arb/resume/" + jobId, {method:"POST"});
                    setScanJob(p => ({...p, paused:false}));
                  } else {
                    await fetch("/discover/csv-arb/pause/" + jobId, {method:"POST"});
                    setScanJob(p => ({...p, paused:true}));
                  }
                }}
                style={{flex:1, padding:"9px 0", fontSize:12, fontWeight:700,
                  background: scanJob?.paused ? "#16a34a22" : "#f9731622",
                  color: scanJob?.paused ? "#16a34a" : "#f97316",
                  border:`1px solid ${scanJob?.paused ? "#16a34a55" : "#f9731655"}`,
                  borderRadius:7, cursor:"pointer"}}
              >
                {scanJob?.paused ? "▶ Devam Et" : "⏸ Duraklat"}
              </button>
              <button
                onClick={async () => {
                  if (!jobId) return;
                  try {
                    // Cancel POST zaten partial sonuçları döndürür
                    const rd = await fetch("/discover/csv-arb/cancel/" + jobId, {method:"POST"}).then(r=>r.json());
                    setScanJob(p => ({...p, scanning:false, paused:false,
                      results: rd.ok !== false ? {...rd, cancelled:true, partial:true} : p?.results}));
                    setDiscoverSubTab("results");  // otomatik sonuçlara geç
                  } catch(e) {
                    setScanJob(p => ({...p, scanning:false, paused:false}));
                  }
                }}
                style={{flex:1, padding:"9px 0", fontSize:12, fontWeight:700,
                  background:"#ef444422", color:"#ef4444",
                  border:"1px solid #ef444455",
                  borderRadius:7, cursor:"pointer"}}
              >
                ⏹ Durdur & Sonuçları Getir
              </button>
            </div>
          </div>
        )}

        {error && <div style={{marginTop:8, color:C.red||"#ef4444", fontSize:11, padding:"6px 8px",
          background:C.surface2, borderRadius:6}}>{error}</div>}
      </div>
    </div>
    )}

    {/* Sub-tab: Results */}
    {discoverSubTab==="results" && (
      <div style={{paddingTop:16}}>
        {results?.cancelled && (
          <div style={{
            padding:"8px 14px", marginBottom:8, borderRadius:6,
            background:"#ef444420", border:"1px solid #ef444450",
            fontSize:12, color:"#ef4444", display:"flex", alignItems:"center", gap:8
          }}>
            <span>⏹</span>
            <span>Tarama durduruldu — <b>{(results.accepted||[]).length}</b> fırsat bulundu
            ({results.stats?.done||0}/{results.stats?.total||0} ISBN tarandı)</span>
          </div>
        )}
        {results?.partial && !results?.cancelled && (
          <div style={{
            padding:"6px 12px", marginBottom:8, borderRadius:6,
            background:"#f97316" + "20", border:"1px solid #f97316" + "40",
            fontSize:11, color:"#f97316", display:"flex", alignItems:"center", gap:6
          }}>
            ⏳ Tarama devam ediyor — <b>{(results.accepted||[]).length}</b> sonuç şimdiye kadar.
            Tarama tamamlandığında tam liste gelecek.
          </div>
        )}
        {!results && !scanning && (
          <div style={{textAlign:"center", paddingTop:80, color:C.muted3, fontSize:13}}>
            <div style={{fontSize:32, marginBottom:12}}>🔍</div>
            <div>ISBN dosyası yükle veya yapıştır, filtrele, tara</div>
            <div style={{marginTop:6, fontSize:11}}>Amazon anlık buybox fiyatlarıyla kıyaslar</div>
            <div style={{marginTop:4, fontSize:11, color:C.muted}}>
              Strict: eBay New/Like New → Amazon New Buybox · eBay Used → Amazon Used Buybox
            </div>
          </div>
        )}

        {results && (
          <>
            {/* Stats bar */}
            <div style={{display:"flex", gap:10, marginBottom:16, flexWrap:"wrap"}}>
              {[
                {label:"Toplam ISBN", val:results.stats?.total_isbns||"?", color:C.text},
                {label:"✅ Kabul", val:results.stats?.accepted_count||0, color:C.green},
                {label:"❌ Elenen", val:results.stats?.rejected_count||0, color:C.muted},
                {label:"⏱ Süre", val:(results.stats?.duration_s||"?")+"s", color:C.blue},
                {label:"✓ GTIN", val:results.stats?.confirmed_count||0, color:"#22c55e"},
                {label:"⚠ Unverified", val:results.stats?.unverified_count||0, color:"#f97316"},
                ...(results.stats?.invalid_input_count > 0 ? [{label:"⚠ Geçersiz ISBN", val:results.stats.invalid_input_count, color:"#ef4444"}] : []),
              ].map(s => (
                <div key={s.label} style={{background:C.surface, border:`1px solid ${C.border}`,
                  borderRadius:8, padding:"8px 14px", minWidth:80}}>
                  <div style={{fontSize:10, color:C.muted}}>{s.label}</div>
                  <div style={{fontSize:16, fontWeight:700, color:s.color}}>{s.val}</div>
                </div>
              ))}
            </div>

            {/* Unverified warning banner */}
            {(results.stats?.unverified_count > 0 || results.stats?.invalid_input_count > 0) && (
              <div style={{background:"#f9731611", border:"1px solid #f9731633", borderRadius:8,
                padding:"8px 14px", marginBottom:10, fontSize:11, color:"#f97316",
                display:"flex", justifyContent:"space-between", alignItems:"center"}}>
                <span>
                  ⚠️ Bu taramada {results.stats?.unverified_count||0} doğrulanamayan{results.stats?.invalid_input_count > 0 ? ` + ${results.stats.invalid_input_count} geçersiz ISBN` : ""} var.
                  {" "}<b>Doğrulanmış sonuçlar için "Sadece Doğrulanmış" filtresi açın.</b>
                </span>
                <button onClick={()=>setVerifiedOnlyFilter(true)}
                  style={{padding:"3px 10px", fontSize:10, borderRadius:5, cursor:"pointer",
                    background:"#f97316", color:"#fff", border:"none", marginLeft:8}}>
                  Sadece ✓GTIN Göster
                </button>
              </div>
            )}

            {/* Verify results summary */}
            {Object.keys(verifyResults).length > 0 && (
              <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,
                padding:"8px 14px",marginBottom:10,display:"flex",gap:12,alignItems:"center",flexWrap:"wrap"}}>
                <span style={{fontSize:11,fontWeight:600,color:C.text}}>🔍 Doğrulama:</span>
                {[
                  {s:"VERIFIED",             e:"✅", col:"#22c55e"},
                  {s:"VERIFIED_STOCK_PHOTO", e:"📷⚠️",col:"#f97316"},
                  {s:"GONE",                 e:"💀", col:"#ef4444"},
                  {s:"PRICE_UP",             e:"📈", col:"#f97316"},
                  {s:"PRICE_DOWN",           e:"📉", col:"#3b82f6"},
                  {s:"MISMATCH",             e:"⚠️", col:"#ef4444"},
                ].map(({s,e,col}) => {
                  const n = Object.values(verifyResults).filter(r=>r.status===s).length;
                  return n > 0 ? (
                    <span key={s} style={{fontSize:11,color:col,fontWeight:600}}>{e} {n} {s}</span>
                  ) : null;
                })}
                <button onClick={()=>setVerifyResults({})}
                  style={{marginLeft:"auto",fontSize:10,padding:"2px 8px",borderRadius:4,
                    background:C.surface2,color:C.muted,border:`1px solid ${C.border}`,cursor:"pointer"}}>
                  Temizle
                </button>
              </div>
            )}

            {/* View toggle + export */}
            <div style={{display:"flex", gap:8, marginBottom:12, alignItems:"center", flexWrap:"wrap"}}>
              {["accepted","rejected"].map(v => (
                <button key={v} onClick={()=>{setActiveView(v);setSelectedRows(new Set());}}
                  style={{padding:"6px 14px", fontSize:11, borderRadius:6, cursor:"pointer",
                    background: activeView===v ? C.accent : C.surface,
                    color: activeView===v ? "#fff" : C.muted,
                    border:`1px solid ${activeView===v ? C.accent : C.border}`}}>
                  {v==="accepted"?`✅ Kabul (${results.accepted.length})`:`❌ Elenen (${results.rejected.length})`}
                </button>
              ))}
              {activeView==="accepted"&&(<>
                <div style={{width:1,height:20,background:C.border,margin:"0 4px"}}/>
                <button onClick={selectedRows.size===results.accepted.length?deselectAll:selectAll}
                  style={{padding:"5px 10px",fontSize:10,borderRadius:5,cursor:"pointer",
                    background:C.surface2,color:C.muted,border:`1px solid ${C.border}`}}>
                  {selectedRows.size===results.accepted.length?"☐ Tümünü Kaldır":"☑ Tümünü Seç"}
                </button>
                {selectedRows.size>0&&(
                  <button onClick={addSelected}
                    style={{padding:"5px 10px",fontSize:10,borderRadius:5,cursor:"pointer",
                      background:"#854d0e22",color:"#f59e0b",border:"1px solid #f59e0b55",fontWeight:600}}>
                    ⭐ {selectedRows.size} Adayı Ekle
                  </button>
                )}
                <div style={{width:1,height:20,background:C.border,margin:"0 4px"}}/>
                <button
                  onClick={verifySelected}
                  disabled={bulkVerifying}
                  style={{padding:"5px 10px",fontSize:10,borderRadius:5,cursor:"pointer",
                    background: bulkVerifying?"#1e293b":"#0ea5e922",
                    color: bulkVerifying?C.muted:"#0ea5e9",
                    border:"1px solid #0ea5e955",fontWeight:600,
                    opacity: bulkVerifying?0.7:1}}>
                  {bulkVerifying ? "⏳ Doğrulanıyor..." : `🔍 ${selectedRows.size>0?selectedRows.size:"Tümünü"} Doğrula`}
                </button>
              </>)}
              <button onClick={exportCsv}
                style={{marginLeft:"auto", padding:"6px 12px", fontSize:11, borderRadius:6,
                  cursor:"pointer", background:C.surface2, color:C.muted, border:`1px solid ${C.border}`}}>
                ⬇ CSV İndir
              </button>
            </div>

            {/* Results table */}
            <div style={{background:C.surface, border:`1px solid ${C.border}`, borderRadius:10, overflow:"hidden"}}>
              {(activeView==="accepted" ? results.accepted : results.rejected).length === 0 ? (
                <div style={{padding:40, textAlign:"center", color:C.muted3, fontSize:12}}>
                  {activeView==="accepted" ? "Filtreden geçen sonuç yok" : "Elenen kayıt yok"}
                </div>
              ) : (
                <div style={{overflowX:"auto"}}>
                  <table style={{width:"100%", borderCollapse:"collapse", fontSize:11}}>
                    <thead>
                      <tr style={{background:C.surface2, borderBottom:`1px solid ${C.border}`}}>
                        {[activeView==="accepted"?"☑":"","ISBN","ASIN","Kaynak","Cond","Alım $","Amazon $","Kar $","ROI %","Tier",activeView==="accepted"?"Güven":"",activeView==="accepted"?"EV/mo":"",activeView==="accepted"?"Worst":"",activeView==="accepted"?"Buyback":"",activeView==="accepted"?"Linkler":"",activeView==="accepted"?"Doğrula":"",activeView==="rejected"?"Sebep":"",activeView==="rejected"?"Aksiyon":""].filter(Boolean).map(h=>(
                          <th key={h} style={{padding:"8px 10px", textAlign:"left", color:C.muted, fontWeight:600, whiteSpace:"nowrap"}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(activeView==="accepted" ? results.accepted : results.rejected)
                        .filter(r => !verifiedOnlyFilter || r.match_quality === "CONFIRMED")
                        .filter(r => !buybackOnlyFilter || (r.buyback_profit != null && r.buyback_profit > 0))
                        .map((r,i)=>(
                        <tr key={i} style={{borderBottom:`1px solid ${C.border}`, background: selectedRows.has(i)?(C.accent+"18"):i%2===0?C.surface:C.surface2, transition:"background .1s"}}>
                          {activeView==="accepted"&&(
                            <td style={{padding:"7px 8px",textAlign:"center",width:32}}>
                              <input type="checkbox" checked={selectedRows.has(i)} onChange={()=>toggleRow(i)}
                                style={{accentColor:C.accent,cursor:"pointer",width:13,height:13}}/>
                            </td>
                          )}
                          <td style={{padding:"7px 10px", color:C.text, fontFamily:"monospace"}}>
                            {r.isbn}
                            {r.match_quality==="CONFIRMED"&&(
                              <span title="GTIN doğrulandı — kesin eşleşme" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#22c55e22",color:"#22c55e",fontFamily:"sans-serif"}}>✓GTIN</span>
                            )}
                            {r.match_quality==="UNVERIFIED_SUPER_DEAL"&&(
                              <span title="Doğrulanamadı — super deal" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#f9731622",color:"#f97316",fontFamily:"sans-serif"}}>⚠DEAL</span>
                            )}
                            {r.match_quality==="UNVERIFIED_INPUT"&&(
                              <span title="Geçersiz ISBN girişi" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#ef444422",color:"#ef4444",fontFamily:"sans-serif"}}>⚠ISBN?</span>
                            )}
                            {r.match_quality==="UNVERIFIED_KEYWORD"&&(
                              <span title="Keyword araması — doğrulanmadı" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#6b728022",color:"#6b7280",fontFamily:"sans-serif"}}>KW</span>
                            )}
                            {r.is_textbook_likely&&(
                              <span title="Textbook olabilir — edition riski ve mevsimsellik yüksek" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#ea580c22",color:"#ea580c",fontFamily:"sans-serif",fontWeight:700}}>📚TB</span>
                            )}
                            {r.has_newer_edition&&(
                              <span title="Daha yeni baskı mevcut — satış zorlaşabilir" style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#dc262622",color:"#dc2626",fontFamily:"sans-serif",fontWeight:700}}>NEW ED⚠</span>
                            )}
                            {r.nyt_bestseller&&(
                              <span title={r.nyt_note||"NYT Bestseller"} style={{marginLeft:4,fontSize:9,padding:"1px 4px",borderRadius:3,background:"#1d4ed822",color:"#1d4ed8",fontFamily:"sans-serif",fontWeight:700}}>
                                📰NYT{r.nyt_weeks>0?` ${r.nyt_weeks}w`:""}
                              </span>
                            )}
                          </td>
                          {/* Verify cell — sadece accepted view'da */}
                          {activeView==="accepted"&&(
                            <td style={{padding:"7px 8px", textAlign:"center", minWidth:90}}>
                              {verifying.has(i) ? (
                                <span style={{fontSize:10,color:C.muted}}>⏳</span>
                              ) : verifyResults[i] ? (
                                <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:3}}>
                                  <span
                                    style={{fontSize:9,fontWeight:700,
                                      color: verifyStatusColor(verifyResults[i].status),
                                      cursor:"pointer", letterSpacing:"0.02em"}}
                                    onClick={()=>verifyOne(i, r)}
                                    title="Yeniden doğrula">
                                    {verifyStatusEmoji(verifyResults[i].status)} {verifyResults[i].status}
                                  </span>
                                  <button
                                    onClick={()=>setVerifyDrawer({rowIdx:i,row:r})}
                                    style={{padding:"1px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                                      background:"#a855f711",color:"#a855f7",
                                      border:"1px solid #a855f744", fontFamily:"var(--mono)"}}>
                                    🧠 Detay
                                  </button>
                                </div>
                              ) : (
                                <button
                                  onClick={()=>verifyOne(i, r)}
                                  title="İlanı 3 adımda doğrula (eBay API → Piyasa → Vision AI)"
                                  style={{padding:"2px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                                    background:"#0ea5e911",color:"#0ea5e9",border:"1px solid #0ea5e944",
                                    fontFamily:"var(--mono)"}}>
                                  🔍 Doğrula
                                </button>
                              )}
                            </td>
                          )}
                          <td style={{padding:"7px 10px", color:C.muted, fontFamily:"monospace", fontSize:10}}>{r.asin||"—"}</td>
                          <td style={{padding:"7px 10px", color:C.accent}}>{r.source}</td>
                          <td style={{padding:"7px 10px"}}>
                            <span style={{padding:"2px 6px", borderRadius:4, fontSize:10, fontWeight:600,
                              background: r.source_condition==="new"?`${C.green}22`:`${C.accent}22`,
                              color: r.source_condition==="new"?C.green:C.accent}}>
                              {r.source_condition?.toUpperCase()||"—"}
                            </span>
                          </td>
                          <td style={{padding:"7px 10px", color:C.text}}>{r.buy_price>0?`$${r.buy_price}`:"—"}</td>
                          <td style={{padding:"7px 10px", color:C.text}}>{r.amazon_sell_price!=null?`$${r.amazon_sell_price}`:"—"}</td>
                          <td style={{padding:"7px 10px", fontWeight:600,
                            color: r.profit>0?C.green:C.red||"#ef4444"}}>{r.profit!==undefined?`$${r.profit}`:"—"}</td>
                          <td style={{padding:"7px 10px", fontWeight:600,
                            color: tierColor(r.roi_tier)}}>{r.roi_pct!=null?`${r.roi_pct}%`:"—"}</td>
                          <td style={{padding:"7px 10px"}}>
                            {r.roi_tier&&<span style={{padding:"2px 6px", borderRadius:4, fontSize:10, fontWeight:700,
                              background:`${tierColor(r.roi_tier)}22`, color:tierColor(r.roi_tier)}}>
                              {r.roi_tier==="fire"?"🔥":r.roi_tier==="good"?"✅":r.roi_tier==="low"?"🔵":"❌"}
                            </span>}
                          </td>
                                                    {activeView==="accepted"&&(
                            <td style={{padding:"7px 10px", textAlign:"center"}}>
                              {r.confidence!=null&&(
                                <span style={{padding:"2px 6px", borderRadius:4, fontSize:10, fontWeight:700,
                                  background: r.confidence>=75?"#22c55e22":r.confidence>=50?"#f97316222":"#ef444422",
                                  color: r.confidence>=75?C.green:r.confidence>=50?"#f97316":"#ef4444"}}>
                                  {r.confidence}
                                </span>
                              )}
                            </td>
                          )}
                          {activeView==="accepted"&&(
                            <td style={{padding:"7px 10px", textAlign:"center", color:C.green, fontWeight:600, fontSize:11}}>
                              {r.ev_score!=null?`$${r.ev_score}`:"—"}
                            </td>
                          )}
                          {activeView==="accepted"&&(
                            <td style={{padding:"7px 10px", textAlign:"center", color:"#f97316", fontSize:11}}>
                              {r.worst_case_profit!=null?`$${r.worst_case_profit}`:"—"}
                              {r.worst_cut_pct!=null&&<span style={{fontSize:9,color:C.muted,marginLeft:2}}>({r.worst_cut_pct}%↓)</span>}
                            </td>
                          )}
                          {/* Buyback channel */}
                          {activeView==="accepted"&&(
                            <td style={{padding:"6px 8px", textAlign:"center", minWidth:90}}>
                              {r.buyback_cash!=null ? (
                                <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:1}}>
                                  <span style={{
                                    fontSize:11, fontWeight:700,
                                    color: r.buyback_profit>0 ? "#22c55e" : "#ef4444",
                                  }}>
                                    ${r.buyback_cash}
                                  </span>
                                  <span style={{fontSize:9, color:C.muted}}>
                                    {r.buyback_profit>0 ? `+$${r.buyback_profit} (${r.buyback_roi}%)` : `−$${Math.abs(r.buyback_profit)}`}
                                  </span>
                                  {r.buyback_vendor && (
                                    <a href={r.buyback_url||"#"} target="_blank" rel="noreferrer"
                                      style={{fontSize:8,color:C.accent,textDecoration:"none",
                                        opacity:0.8,maxWidth:80,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                                      {r.buyback_vendor}
                                    </a>
                                  )}
                                  {r.buyback_trend && r.buyback_trend!=="unknown" && (
                                    <span title={r.buyback_trend_note||""} style={{
                                      fontSize:8, padding:"1px 4px", borderRadius:3, fontWeight:700,
                                      background: r.buyback_trend==="rising"?"#14532d20":r.buyback_trend==="falling"?"#7f1d1d20":"#1e293b30",
                                      color: r.buyback_trend==="rising"?"#22c55e":r.buyback_trend==="falling"?"#ef4444":"#94a3b8",
                                    }}>
                                      {r.buyback_trend==="rising"?"↑":r.buyback_trend==="falling"?"↓":"→"}
                                    </span>
                                  )}
                                </div>
                              ) : (
                                <span style={{fontSize:10,color:C.muted3}}>—</span>
                              )}
                            </td>
                          )}
                          {activeView==="accepted"&&(
                            <td style={{padding:"6px 8px"}}>
                              <SourceLinks isbn={r.isbn} asin={r.asin} C={C}
                              bookTitle={r.google_title||r.ebay_title||""}
                              bookAuthor={(r.edition_authors||[]).join(" ")||""}
                            />
                            </td>
                          )}
                          {activeView==="accepted"&&(
                            <td style={{padding:"6px 8px",textAlign:"center"}}>
                              <button onClick={()=>isCandidate(r)?null:addCandidate(r)}
                                title={isCandidate(r)?"Zaten aday listesinde":"Aday listesine ekle"}
                                style={{background:isCandidate(r)?"#f59e0b33":"transparent",
                                  border:`1px solid ${isCandidate(r)?"#f59e0b":"#f59e0b55"}`,
                                  borderRadius:5,padding:"3px 7px",cursor:isCandidate(r)?"default":"pointer",
                                  fontSize:13,lineHeight:1,transition:"all .15s",
                                  opacity:isCandidate(r)?1:0.6}}>
                                {isCandidate(r)?"⭐":"☆"}
                              </button>
                            </td>
                          )}
                          {activeView==="rejected"&&(
                            <td style={{padding:"7px 10px", color:C.muted, fontSize:10, maxWidth:200, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap"}}
                              title={r.reason}>
                              {fmtReason(r.reason)}
                            </td>
                          )}
                          {activeView==="rejected"&&(
                            <td style={{padding:"6px 8px",textAlign:"center"}}>
                              <button onClick={()=>addCandidate(r)}
                                title="Aday listesine ekle (filtreyi atla)"
                                style={{background:isCandidate(r)?"#f59e0b33":"transparent",
                                  border:`1px solid ${isCandidate(r)?"#f59e0b":"#f59e0b55"}`,
                                  borderRadius:5,padding:"3px 7px",cursor:isCandidate(r)?"default":"pointer",
                                  fontSize:13,lineHeight:1,opacity:isCandidate(r)?1:0.5}}>
                                {isCandidate(r)?"⭐":"☆"}
                              </button>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    )}

    {/* Sub-tab: Candidates */}
    {discoverSubTab==="candidates" && (
      <div style={{paddingTop:16}}>
        <CandidatesTab
          C={C}
          candidates={candidates}
          removeCandidate={removeCandidate}
          saveCandidates={saveCandidates}
          push={push}
          isbns={watchlistIsbns}
          addIsbn={addIsbn}
        />
      </div>
    )}

    {/* Global verify drawer */}
    {verifyDrawer && verifyResults[verifyDrawer.rowIdx] && (
      <VerifyDetailDrawer C={C} data={verifyResults[verifyDrawer.rowIdx]}
        row={verifyDrawer.row} onClose={()=>setVerifyDrawer(null)}/>
    )}
  </>
  );
}

// ─── Verify Detail Drawer ──────────────────────────────────────────────────
function VerifyDetailDrawer({ C, data, onClose, row }) {
  if (!data) return null;
  const { ebay = {}, market = {}, vision = {}, status, summary, checked_at } = data;

  const STATUS_COLOR = {
    VERIFIED:"#22c55e", VERIFIED_STOCK_PHOTO:"#f97316",
    GONE:"#ef4444", PRICE_UP:"#f97316", PRICE_DOWN:"#3b82f6",
    MISMATCH:"#ef4444", ERROR:"#94a3b8", SKIP:"#94a3b8", UNVERIFIABLE:"#64748b", SEARCHED:"#3b82f6",
    MATCH:"#22c55e", UNCERTAIN:"#eab308", STOCK_PHOTO:"#f97316", NO_IMAGE:"#94a3b8",
  };

  const sc = (s) => STATUS_COLOR[s] || "#94a3b8";

  /* ── tiny helpers ────────────────────────────────────────── */
  const Pill = ({ s }) => (
    <span style={{
      padding:"2px 8px", borderRadius:99, fontSize:10, fontWeight:700,
      background: sc(s)+"22", color: sc(s), whiteSpace:"nowrap",
    }}>{s||"?"}</span>
  );

  const SmTag = ({ children, col="#94a3b8" }) => (
    <span style={{
      padding:"1px 6px", borderRadius:99, fontSize:9,
      background: col+"18", color: col, whiteSpace:"nowrap",
    }}>{children}</span>
  );

  const ProvBadge = ({ provider="", model="" }) => {
    const map = {groq:"#f55036",cerebras:"#6366f1",openrouter:"#10b981",gemini:"#4285f4",google:"#4285f4"};
    const k = Object.keys(map).find(k => provider.toLowerCase().includes(k));
    const col = map[k] || "#94a3b8";
    const m = model.split("/").pop().replace(/-\d{8}$/,"").slice(0,20);
    return <SmTag col={col}>🤖 {provider}{m ? " · "+m : ""}</SmTag>;
  };

  const LiveCacheTag = ({ fromCache, ageS }) =>
    fromCache
      ? <SmTag col="#a855f7">⚡ cache · {Math.round((ageS||0)/60)}dk</SmTag>
      : <SmTag col="#64748b">🌐 live</SmTag>;

  /* ── data row (label left, value right) ─────────────────── */
  const R = ({ label, val, danger, mono }) => (
    <div style={{
      display:"grid", gridTemplateColumns:"100px 1fr",
      gap:8, padding:"5px 16px", alignItems:"start",
    }}>
      <span style={{color:C.muted, fontSize:10, paddingTop:1, flexShrink:0}}>{label}</span>
      <span style={{
        fontSize:11, color: danger ? "#ef4444" : C.text,
        textAlign:"right", wordBreak:"break-word", lineHeight:1.5,
        fontFamily: mono ? "var(--mono)" : undefined,
      }}>{val}</span>
    </div>
  );

  /* ── section wrapper ─────────────────────────────────────── */
  const Section = ({ num, title, statusKey, accent, rightTag, children, skipMsg }) => {
    const col = accent || sc(statusKey);
    return (
      <div style={{borderTop:`1px solid ${C.border}`, paddingBottom:6}}>
        <div style={{background:`${col}0e`, padding:"8px 16px 6px"}}>
          {/* Row 1: step label left, tag right */}
          <div style={{display:"flex", alignItems:"center", justifyContent:"space-between", marginBottom:4}}>
            <span style={{fontSize:9, fontWeight:700, letterSpacing:"0.1em", color:col, textTransform:"uppercase"}}>
              Adım {num}
            </span>
            {rightTag && <div>{rightTag}</div>}
          </div>
          {/* Row 2: title + status pill — no rightTag competing */}
          <div style={{display:"flex", alignItems:"center", gap:8, flexWrap:"wrap"}}>
            <span style={{fontSize:12, fontWeight:700, color:C.text}}>{title}</span>
            {statusKey && <Pill s={statusKey}/>}
          </div>
        </div>
        {skipMsg
          ? <div style={{padding:"7px 16px", color:C.muted, fontSize:10}}>{skipMsg}</div>
          : children
        }
      </div>
    );
  };

  const checkedAt = checked_at ? new Date(checked_at*1000).toLocaleTimeString("tr-TR") : null;
  const sumCol = sc(status);
  const isSkipVision = !vision.verdict || vision.verdict==="NO_IMAGE" || vision.status==="SKIP";

  return (
    <div
      style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.25)",zIndex:900,display:"flex",justifyContent:"flex-end"}}
      onClick={onClose}
    >
      <div
        onClick={e=>e.stopPropagation()}
        style={{
          width:400, maxWidth:"95vw", height:"100vh",
          background:C.cardBg, borderLeft:`1px solid ${C.border}`,
          overflowY:"auto", display:"flex", flexDirection:"column",
          fontFamily:"var(--mono)",
        }}
      >
        {/* ── top bar ── */}
        <div style={{
          position:"sticky", top:0, zIndex:2,
          background:C.cardBg, borderBottom:`1px solid ${C.border}`,
          padding:"11px 14px",
          display:"flex", alignItems:"center", justifyContent:"space-between",
        }}>
          <div>
            <div style={{fontSize:13, fontWeight:700, color:C.text}}>Doğrulama Detayı</div>
            <div style={{fontSize:10, color:C.muted, marginTop:1}}>
              {row?.isbn}{checkedAt ? " · "+checkedAt : ""}
            </div>
          </div>
          <div style={{display:"flex", alignItems:"center", gap:8}}>
            <Pill s={status}/>
            <button
              onClick={onClose}
              style={{background:"none",border:"none",cursor:"pointer",color:C.muted,fontSize:20,lineHeight:1,padding:0}}
            >×</button>
          </div>
        </div>

        {/* ── summary ── */}
        <div style={{
          padding:"9px 14px 9px 16px",
          borderLeft:`3px solid ${sumCol}`,
          background:`${sumCol}0d`,
          borderBottom:`1px solid ${C.border}`,
          fontSize:11, color:C.text, lineHeight:1.6,
        }}>
          {summary||"Doğrulama tamamlandı."}
        </div>

        {/* ── Step 1: eBay API ── */}
        <Section
          num={1} title="eBay API"
          statusKey={ebay.status}
          accent={ebay.status==="GONE"?"#ef4444":ebay.status?.includes("PRICE")?"#f97316":"#22c55e"}
          rightTag={<LiveCacheTag fromCache={false}/>}
          skipMsg={
            ebay.status==="SKIP"
              ? (ebay.reason==="not_ebay" ? "eBay ilanı değil — bu adım atlandı" : "eBay adımı atlandı")
              : null
          }
        >
          {ebay.item_title  && <R label="İlan"       val={ebay.item_title}/>}
          {ebay.current_price!=null && (
            <R label="Anlık fiyat" val={
              <span>
                ${ebay.current_price?.toFixed(2)}
                {ebay.price_delta!=null && (
                  <span style={{color:ebay.price_delta>0?"#f97316":"#22c55e",marginLeft:6}}>
                    {ebay.price_delta>0?"+":""}{ebay.price_delta?.toFixed(2)} ({ebay.price_delta_pct?.toFixed(1)}%)
                  </span>
                )}
              </span>
            }/>
          )}
          {ebay.searched_by==="isbn_search" && (
            <div style={{margin:"4px 16px 4px",padding:"6px 10px",borderRadius:6,
              borderLeft:"3px solid #3b82f6",background:"#3b82f610",
              fontSize:10,color:"#60a5fa",lineHeight:1.5}}>
              🔍 ilan ID eksikti — ISBN ile yeniden arama yapıldı ({ebay.total_listings||0} aktif ilan bulundu)
            </div>
          )}
          {ebay.isbn_check && (
            ebay.isbn_check === "UNKNOWN"
              ? <R label="ISBN" val={<span style={{fontSize:10,color:"#94a3b8"}}>— eşleşme verisi yok (normal)</span>}/>
              : <R label="ISBN" val={<Pill s={ebay.isbn_check}/>}/>
          )}
          {ebay.condition   && <R label="Kondisyon"  val={ebay.condition}/>}
          {ebay.reason && ebay.status!=="VERIFIED" && <R label="Neden" val={ebay.reason} danger/>}
        </Section>

        {/* ── Step 2: Market ── */}
        <Section
          num={2} title="Piyasa Fiyatı"
          statusKey={market.status}
          accent={market.status==="ERROR"?"#94a3b8":market.status==="PRICE_UP"?"#f97316":market.status==="PRICE_DOWN"?"#3b82f6":"#a855f7"}
          rightTag={<LiveCacheTag fromCache={market.from_cache} ageS={market.cache_age_s}/>}
          skipMsg={market.status==="SKIP"?"Piyasa verisi atlandı":null}
        >
          {market.status==="ERROR"
            ? <>
                <R label="Durum" val={
                  market.reason==="ip_blocked"
                    ? <span style={{color:"#f97316",fontWeight:600}}>⚠️ IP engellendi</span>
                    : <span style={{color:"#94a3b8"}}>{market.reason||"no_prices_found"}</span>
                }/>
                {market.hint && <R label="Açıklama" val={market.hint}/>}
                {market.reason==="ip_blocked" && (
                  <div style={{margin:"4px 16px 6px",padding:"6px 10px",borderRadius:6,
                    borderLeft:"3px solid #f97316",background:"#f9731610",
                    fontSize:10,color:"#f97316",lineHeight:1.5}}>
                    BookFinder/AbeBooks sunucu IP'nizi engelliyor. Piyasa fiyatı doğrulaması yapılamıyor.
                  </div>
                )}
              </>
            : <>
                {market.cheapest_found!=null && (
                  <R label="En ucuz" val={
                    <span>
                      ${market.cheapest_found?.toFixed(2)}
                      {market.cheapest_source&&<span style={{color:C.muted,marginLeft:5}}>@ {market.cheapest_source}</span>}
                    </span>
                  }/>
                )}
                {market.expected_price!=null && <R label="Beklenen" val={`$${market.expected_price?.toFixed(2)}`}/>}
                {market.price_delta!=null && (
                  <R label="Fark" val={
                    <span style={{color:market.price_delta>0?"#f97316":"#22c55e"}}>
                      {market.price_delta>0?"+":""}{market.price_delta?.toFixed(2)} ({market.price_delta_pct?.toFixed(1)}%)
                    </span>
                  }/>
                )}
                {market.data_source && <R label="Kaynak" val={market.data_source}/>}
              </>
          }
        </Section>

        {/* ── Step 3: Vision AI ── */}
        <Section
          num={3} title="Görsel AI"
          statusKey={vision.verdict||vision.status}
          accent={vision.verdict==="MATCH"?"#22c55e":vision.verdict==="MISMATCH"?"#ef4444":vision.verdict==="STOCK_PHOTO"?"#f97316":"#94a3b8"}
          rightTag={vision.provider ? <ProvBadge provider={vision.provider} model={vision.model||""}/> : null}
          skipMsg={isSkipVision ? (vision.notes||"Görsel doğrulama atlandı") : null}
        >
          {vision.confidence!=null && (
            <R label="Güven" val={
              <span style={{color:vision.confidence>70?"#22c55e":vision.confidence>40?"#eab308":"#ef4444",fontWeight:700}}>
                %{vision.confidence}
              </span>
            }/>
          )}
          {vision.notes         && <R label="Not"          val={vision.notes}/>}
          {vision.title_visible !=null && <R label="Başlık" val={vision.title_visible ?"✅ Evet":"❌ Hayır"}/>}
          {vision.author_visible!=null && <R label="Yazar"  val={vision.author_visible?"✅ Evet":"❌ Hayır"}/>}
          {vision.is_stock_photo!=null && (
            <R label="Stock foto" val={
              <span style={{color:vision.is_stock_photo?"#f97316":"#22c55e"}}>
                {vision.is_stock_photo?"⚠️ Evet":"✅ Hayır"}
              </span>
            }/>
          )}
          {vision.condition_notes && <R label="Kondisyon" val={vision.condition_notes}/>}
          {vision.stock_photo_risk && (
            <div style={{
              margin:"4px 14px 4px", padding:"7px 10px", borderRadius:6,
              borderLeft:"3px solid #f97316", background:"#f9731610",
              fontSize:10, color:"#f97316", lineHeight:1.5,
            }}>
              ⚠️ Stock fotoğraf + used kondisyon — gerçek durum gizli olabilir.
            </div>
          )}
        </Section>

        <div style={{height:28}}/>
      </div>
    </div>
  );
}


function AlertsFeedTab({ C, theme, push, isbns, titles, bookMeta = {} }) {
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [isbnFilter, setIsbnFilter] = useState("");
  const [condFilter, setCondFilter] = useState("");
  const [decisionFilter, setDecisionFilter] = useState("");
  const [sortBy, setSortBy] = useState("ts");
  const [groupByIsbn, setGroupByIsbn] = useState(true);   // group feed by ISBN
  const [expandedIsbns, setExpandedIsbns] = useState({});  // { isbn: bool }
  const [mutedIsbns, setMutedIsbns] = useState({});        // { isbn: unmuteTs }
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [lightboxSrc, setLightboxSrc] = useState(null);
  const [drawerData, setDrawerData] = useState(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [soldScrape, setSoldScrape] = useState({});       // { [isbn]: {loading,data,error} }
  const _prefetchCache = useRef({});   // { [isbn]: data } — in-memory, avoids re-fetch within session
  const [dedupIsbn, setDedupIsbn] = useState("");

  const openDrawer = useCallback(async (e) => {
    setSelectedAlert(e);
    // Cache-first: if already prefetched, show instantly
    const cached = _prefetchCache.current[e.isbn];
    if (cached) {
      setDrawerData(cached);
      setDrawerLoading(false);
      return;
    }
    setDrawerData(null);
    setDrawerLoading(true);
    try {
      const d = await req(`/alerts/details?isbn=${e.isbn}&ebay_item_id=${e.item_id||""}`);
      _prefetchCache.current[e.isbn] = d;
      setDrawerData(d);
    } catch(err) {
      setDrawerData({ ok: false, error: err.message });
    } finally {
      setDrawerLoading(false);
    }
  }, []);

  const fetchSoldScrape = async (isbn) => {
    setSoldScrape(s => ({...s, [isbn]: {loading:true, data:null, error:null}}));
    try {
      const d = await req(`/ebay/sold-avg/${isbn}`, {}, 25000);
      setSoldScrape(s => ({...s, [isbn]: {loading:false, data:d, error:null}}));
    } catch(e) {
      setSoldScrape(s => ({...s, [isbn]: {loading:false, data:null, error:e.message}}));
    }
  };

  const [bfData, setBfData] = useState({});  // { [isbn]: {loading, data, error, condition} }
  const fetchBookfinder = async (isbn, condition = "all") => {
    setBfData(s => ({...s, [isbn]: {loading:true, data:null, error:null, condition}}));
    try {
      const d = await req(`/bookfinder/${isbn}?condition=${condition}`, {}, 35000);
      setBfData(s => ({...s, [isbn]: {loading:false, data:d, error:null, condition}}));
    } catch(e) {
      setBfData(s => ({...s, [isbn]: {loading:false, data:null, error:e.message, condition}}));
    }
  };

  const load = async () => {
    setLoading(true);
    try {
      const url = isbnFilter ? `/alerts/history?limit=100&isbn=${isbnFilter}` : "/alerts/history?limit=100";
      const [h, s] = await Promise.allSettled([req(url), req("/alerts/summary")]);
      if (h.status === "fulfilled") {
        const loaded = h.value.entries || [];
        setEntries(loaded);
        // Pre-warm details cache for top unique ISBNs (background, silent)
        // Staggered to avoid hammering — 400ms between each
        const seen = new Set();
        const toWarm = [];
        for (const e of loaded) {
          if (!seen.has(e.isbn) && !_prefetchCache.current[e.isbn]) {
            seen.add(e.isbn);
            toWarm.push(e.isbn);
            if (toWarm.length >= 6) break;  // max 6 ISBNs per load cycle
          }
        }
        toWarm.forEach((isbn, i) => {
          setTimeout(async () => {
            try {
              const d = await req(`/alerts/details?isbn=${isbn}`, {}, 20000);
              if (d?.ok) _prefetchCache.current[isbn] = d;
            } catch { /* silent — prefetch failure is non-critical */ }
          }, i * 450);  // stagger: 0ms, 450ms, 900ms …
        });
      }
      if (s.status === "fulfilled") setSummary(s.value);
    } catch(e) { push("Yüklenemedi: "+e.message, "error"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [isbnFilter]);

  const clearDedup = async () => {
    if (!dedupIsbn) { push("ISBN seç", "error"); return; }
    try {
      await req(`/alerts/dedup/${dedupIsbn}`, {method:"DELETE"});
      push(`${dedupIsbn} tekrar gönderilmek üzere işaretlendi — scheduler bir ⏭ sonraki taramada alert atar`, "success");
    } catch(e) { push("Hata: "+e.message, "error"); }
  };

  const injectTest = async () => {
    try {
      await req("/debug/inject-history", {method:"POST"});
      push("Test entry eklendi", "success");
      await load();
    } catch(e) { push(e.message, "error"); }
  };

  const condLabel = { brand_new:"New", like_new:"Like New", very_good:"Very Good", good:"Good", acceptable:"Acceptable", used_all:"Used" };
  const condColor = (b, C) => ({brand_new:C.green, like_new:C.blue, very_good:C.purple, good:C.accent, acceptable:C.orange, used_all:C.muted})[b] || C.muted;

  const fmtTs = (ts) => {
    const diff = Math.round((Date.now() - ts*1000)/1000);
    if (diff < 60)   return `${diff}s önce`;
    if (diff < 3600) return `${Math.round(diff/60)}dk önce`;
    if (diff < 86400) return `${Math.round(diff/3600)}s önce`;
    return new Date(ts*1000).toLocaleDateString("tr-TR");
  };

  return (
    <div>
      {/* Summary cards */}
      {summary && (
        <div style={{display:"grid",gridTemplateColumns:"repeat(3,1fr)",gap:14,marginBottom:20}}>
          <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:10,padding:"16px 20px"}}>
            <div style={{fontSize:10,color:C.muted,marginBottom:4}}>Toplam Alert</div>
            <div style={{fontSize:28,fontWeight:600,color:C.text}}>{summary.total}</div>
          </div>
          <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:10,padding:"16px 20px"}}>
            <div style={{fontSize:10,color:C.muted,marginBottom:4}}>Son 24 Saat</div>
            <div style={{fontSize:28,fontWeight:600,color:C.green}}>{summary.last_24h}</div>
          </div>
          <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:10,padding:"16px 20px"}}>
            <div style={{fontSize:10,color:C.muted,marginBottom:4}}>ISBN Sayısı</div>
            <div style={{fontSize:28,fontWeight:600,color:C.blue}}>{Object.keys(summary.by_isbn||{}).length}</div>
          </div>
        </div>
      )}

      {/* Controls row: filter + dedup + refresh */}
      <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:10,padding:"14px 16px",marginBottom:16,display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
        {/* ISBN filter */}
        <select className="inp" value={isbnFilter} onChange={e=>setIsbnFilter(e.target.value)} style={{flex:"1 1 200px",minWidth:160,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.text,fontSize:12}}>
          <option value="">Tüm ISBNler</option>
          {isbns.map(isbn=><option key={isbn} value={isbn}>{isbn}{titles[isbn]?` — ${titles[isbn]}`:""}</option>)}
        </select>

        {/* Condition filter */}
        <select className="inp" value={condFilter} onChange={e=>setCondFilter(e.target.value)} style={{flex:"0 1 130px",minWidth:110,background:C.inputBg,border:`1px solid ${condFilter?C.accent:C.inputBorder}`,color:condFilter?C.accent:C.text,fontSize:12}}>
          <option value="">Kondisyon</option>
          <option value="brand_new">New</option>
          <option value="like_new">Like New</option>
          <option value="very_good">Very Good</option>
          <option value="good">Good</option>
          <option value="acceptable">Acceptable</option>
          <option value="used_all">Used</option>
        </select>

        {/* Decision filter */}
        <select className="inp" value={decisionFilter} onChange={e=>setDecisionFilter(e.target.value)} style={{flex:"0 1 90px",minWidth:80,background:C.inputBg,border:`1px solid ${decisionFilter?C.blue:C.inputBorder}`,color:decisionFilter?C.blue:C.text,fontSize:12}}>
          <option value="">Karar</option>
          <option value="BUY">🟢 BUY</option>
          <option value="OFFER">🟡 OFFER</option>
        </select>

        {/* Sort */}
        <select className="inp" value={sortBy} onChange={e=>setSortBy(e.target.value)} style={{flex:"0 1 110px",minWidth:90,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.text,fontSize:12}}>
          <option value="ts">En yeni</option>
          <option value="score">Score ↓</option>
          <option value="total">Fiyat ↑</option>
        </select>

        {/* Group toggle */}
        <button
          onClick={()=>setGroupByIsbn(g=>!g)}
          title={groupByIsbn ? "ISBN grubunu aç (düz liste)" : "ISBN'e göre grupla"}
          style={{background:groupByIsbn?C.accent:"none",border:`1px solid ${groupByIsbn?C.accent:C.border}`,borderRadius:5,color:groupByIsbn?C.accentText:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 10px",cursor:"pointer",whiteSpace:"nowrap"}}>
          {groupByIsbn ? "⊞ Grubu Kaldır" : "⊟ ISBN'e Göre Grupla"}
        </button>

        <div style={{width:1,height:24,background:C.border,flexShrink:0}}/>

        {/* Dedup clear — proper React state */}
        <select className="inp" value={dedupIsbn} onChange={e=>setDedupIsbn(e.target.value)} style={{flex:"1 1 180px",minWidth:150,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.text,fontSize:12}}>
          <option value="">Tekrar bildirimi sıfırla…</option>
          {isbns.map(i=><option key={i} value={i}>{i}</option>)}
        </select>
        <button
          onClick={clearDedup}
          disabled={!dedupIsbn}
          title="Seçili ISBN'in tekrar kaydını sil — scheduler bir ⏭ sonraki taramada yeniden alert gönderir"
          style={{background:"none",border:`1px solid ${dedupIsbn?C.orange:C.border}`,borderRadius:5,color:dedupIsbn?C.orange:C.muted3,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:dedupIsbn?"pointer":"default",whiteSpace:"nowrap",transition:"all .15s"}}
        >
          🔕 Sıfırla
        </button>

        <div style={{width:1,height:24,background:C.border,flexShrink:0}}/>

        <button onClick={injectTest} title="Test için sahte alert history entry ekle" style={{background:"none",border:`1px solid ${C.border}`,borderRadius:5,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 10px",cursor:"pointer"}}>
          💉
        </button>
        <button onClick={load} disabled={loading} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:5,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 10px",cursor:"pointer"}}>
          {loading ? "⟳" : "↻"}
        </button>
        <span style={{fontSize:10,color:C.muted3,whiteSpace:"nowrap"}}>
          {(condFilter||decisionFilter)
            ? `${entries.filter(e=>(!condFilter||e.condition===condFilter)&&(!decisionFilter||e.decision===decisionFilter)).length}/${entries.length}`
            : entries.length} kayıt · 30s
        </span>
      </div>

      {/* Active filter chips */}
      {(condFilter || decisionFilter) && (
        <div style={{display:"flex",gap:8,marginBottom:12,flexWrap:"wrap",alignItems:"center"}}>
          <span style={{fontSize:10,color:C.muted3}}>Filtre aktif:</span>
          {condFilter && (
            <span style={{fontSize:10,background:C.surface2,border:`1px solid ${C.border}`,borderRadius:3,padding:"2px 8px",color:C.text,display:"flex",alignItems:"center",gap:4}}>
              {condFilter.replace(/_/g," ")}
              <button onClick={()=>setCondFilter("")} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",padding:0,fontSize:12,lineHeight:1}}>×</button>
            </span>
          )}
          {decisionFilter && (
            <span style={{fontSize:10,background:C.surface2,border:`1px solid ${C.border}`,borderRadius:3,padding:"2px 8px",color:C.text,display:"flex",alignItems:"center",gap:4}}>
              {decisionFilter}
              <button onClick={()=>setDecisionFilter("")} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",padding:0,fontSize:12,lineHeight:1}}>×</button>
            </span>
          )}
          <button onClick={()=>{setCondFilter("");setDecisionFilter("");}} style={{fontSize:10,background:"none",border:"none",color:C.muted3,cursor:"pointer",textDecoration:"underline"}}>tümünü kaldır</button>
        </div>
      )}

      {entries.length === 0 && !loading && (
        <div style={{border:`1px dashed ${C.border}`,borderRadius:8,padding:32,textAlign:"center",color:C.muted3,fontSize:12,lineHeight:2}}>
          Henüz alert geçmişi yok.<br/>
          <span style={{fontSize:11,color:C.muted}}>
            Scheduler bir deal bulup Telegram'a gönderdikten sonra burada görünür.
          </span><br/>
          <span style={{fontSize:10}}>
            Test için: <b style={{color:C.accent}}>💉</b> butonu · Dedup dolu olabilir: dropdown'dan ISBN seç → <b style={{color:C.orange}}>🔕 Sıfırla</b>
          </span>
        </div>
      )}

      {/* ── Alert entries — grouped or flat ─────────────────────────── */}
      {(() => {
        const now = Date.now();
        const filtered = entries
          .filter(e => !condFilter     || e.condition === condFilter)
          .filter(e => !decisionFilter || e.decision  === decisionFilter)
          .filter(e => {
            const muteUntil = mutedIsbns[e.isbn];
            return !muteUntil || now > muteUntil;
          });

        const sorted = [...filtered].sort((a, b) => {
          if (sortBy === "score") return (b.deal_score ?? -1) - (a.deal_score ?? -1);
          if (sortBy === "total") return a.total - b.total;
          return b.ts - a.ts;
        });

        const renderRow = (e, i) => {
          const cc = condColor(e.condition, C);
          const isBuy = e.decision === "BUY";
          const delta = e.limit != null ? Math.round(e.limit - e.total) : null;
          const isSelected = selectedAlert?.item_id === e.item_id;
          return (
            <div
              key={`${e.item_id}-${i}`}
              onClick={() => openDrawer(e)}
              style={{
                display:"grid",
                gridTemplateColumns:"72px 1fr 160px",
                gap:0,
                background: isSelected ? (theme==="dark"?"#13131c":C.surface) : C.rowBg,
                border:`1px solid ${isSelected ? cc : C.rowBorder}`,
                borderLeft:`3px solid ${cc}`,
                borderRadius:10,
                marginBottom:6,
                overflow:"hidden",
                cursor:"pointer",
                transition:"background .12s, border-color .12s",
                minHeight:80,
              }}
            >
              <Thumb imageUrl={e.image_url} isbn={e.isbn} href={e.url} C={C} size={72}/>
              <div style={{padding:"9px 12px",minWidth:0,display:"flex",flexDirection:"column",justifyContent:"center",gap:4}}>
                <div style={{display:"grid",gridTemplateColumns:"1fr auto",alignItems:"center",gap:8,minWidth:0}}>
                  <span style={{fontSize:12,fontWeight:600,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                    {e.title || e.isbn}
                  </span>
                  <div style={{display:"inline-flex",alignItems:"center",gap:5,flexShrink:0}}>
                    <span style={{fontSize:10,color:cc,border:`1px solid ${cc}`,borderRadius:3,padding:"1px 5px",lineHeight:1.5,whiteSpace:"nowrap"}}>
                      {condLabel[e.condition]||e.condition}
                    </span>
                    {e.match_quality==="CONFIRMED" && <span title="ISBN GTIN doğrulandı" style={{fontSize:11,color:C.green}}>✓</span>}
                    {e.match_quality==="UNVERIFIED_SUPER_DEAL" && <span title="Unverified — super deal" style={{fontSize:11,color:C.orange}}>⚠</span>}
                  </div>
                </div>
                <div style={{fontSize:10,color:C.muted3,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                  <code>{e.isbn}</code>
                  {titles[e.isbn] && <span style={{marginLeft:6,color:C.muted}}>{titles[e.isbn]}</span>}
                </div>
                <div style={{display:"flex",gap:8,flexWrap:"wrap",alignItems:"center"}}>
                  <span style={{fontSize:12,fontWeight:700,color:C.text}}>${e.total}</span>
                  <span style={{fontSize:10,color:C.muted3}}>lim ${e.limit}</span>
                  {delta != null && (
                    <span style={{fontSize:10,fontWeight:600,padding:"0 5px",borderRadius:3,
                      color:delta>=0?C.green:C.red,
                      background:delta>=0?"rgba(52,211,153,.1)":"rgba(248,113,113,.1)",
                      border:`1px solid ${delta>=0?C.green:C.red}`,
                    }}>
                      {delta>=0?`-$${delta}`:`+$${Math.abs(delta)}`}
                    </span>
                  )}
                  {e.sold_avg!=null && <span style={{fontSize:10,color:C.muted}}>sold ~${Math.round(e.sold_avg)}</span>}
                  {e.ship_estimated && <span style={{fontSize:10,color:C.orange}}>⚠ est.ship</span>}
                </div>
              </div>
              <div style={{padding:"9px 12px",display:"flex",flexDirection:"column",alignItems:"flex-end",justifyContent:"space-between",borderLeft:`1px solid ${C.border}`,gap:4}}>
                <ScoreRing score={e.deal_score} C={C}/>
                <div style={{display:"inline-flex",alignItems:"center",gap:6}}>
                  <span style={{fontSize:11,fontWeight:700,padding:"2px 9px",borderRadius:20,whiteSpace:"nowrap",
                    background:isBuy?"rgba(52,211,153,.15)":"rgba(96,165,250,.15)",
                    color:isBuy?C.green:C.blue,
                    border:`1px solid ${isBuy?C.green:C.blue}`,
                  }}>
                    {isBuy?"BUY":"OFFER"}
                  </span>
                  {e.url && (
                    <a href={e.url} target="_blank" rel="noreferrer" onClick={ev=>ev.stopPropagation()} style={{
                      fontSize:11,color:C.accent,textDecoration:"none",
                      border:`1px solid ${C.accent}`,borderRadius:5,
                      padding:"2px 8px",whiteSpace:"nowrap",fontWeight:500,
                    }}>eBay ↗</a>
                  )}
                </div>
                <span style={{fontSize:10,color:C.muted3}}>{fmtTs(e.ts)}</span>
              </div>
            </div>
          );
        };

        if (!groupByIsbn) {
          return sorted.map(renderRow);
        }

        // ── Grouped view ─────────────────────────────────────────────────────
        // Group by ISBN, pick best score per group as header
        const groups = {};
        for (const e of sorted) {
          if (!groups[e.isbn]) groups[e.isbn] = [];
          groups[e.isbn].push(e);
        }

        return Object.entries(groups).map(([isbn, rows]) => {
          const best = rows.reduce((a,b) => (b.deal_score??0) > (a.deal_score??0) ? b : a, rows[0]);
          const isExpanded = !!expandedIsbns[isbn];
          const score = best.deal_score;
          const scoreTier = score >= 75 ? "fire" : score >= 50 ? "good" : "low";
          const tierEmoji = {fire:"🔥",good:"✨",low:"·"}[scoreTier];
          const tierColor = {fire:C.green,good:C.accent,low:C.muted}[scoreTier];
          const isMuted = mutedIsbns[isbn] && Date.now() < mutedIsbns[isbn];

          return (
            <div key={isbn} style={{marginBottom:8}}>
              {/* Group header */}
              <div style={{
                display:"flex",alignItems:"center",gap:8,
                background:C.surface2,border:`1px solid ${C.border}`,
                borderRadius:isExpanded?"8px 8px 0 0":"8px",
                padding:"8px 12px",cursor:"pointer",
              }}>
                <div onClick={()=>setExpandedIsbns(ex=>({...ex,[isbn]:!ex[isbn]}))} style={{flex:1,display:"flex",alignItems:"center",gap:8,minWidth:0}}>
                  <span style={{fontSize:12,color:C.muted3,flexShrink:0}}>{isExpanded?"▾":"▸"}</span>
                  <span style={{fontSize:12,fontWeight:600,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                    {titles[isbn] || isbn}
                  </span>
                  <span style={{fontSize:10,color:C.muted3,flexShrink:0,fontFamily:"var(--mono)"}}>{isbn}</span>
                  <span style={{fontSize:10,background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"1px 7px",color:C.muted,flexShrink:0}}>
                    {rows.length} alert
                  </span>
                </div>
                <div style={{display:"flex",alignItems:"center",gap:6,flexShrink:0}}>
                  {score != null && (
                    <span style={{fontSize:11,fontWeight:700,color:tierColor}}>{tierEmoji} {score}</span>
                  )}
                  <span style={{fontSize:10,color:C.muted3}}>${best.total}</span>
                  {/* Mute 24h button */}
                  <button
                    title={isMuted ? "Sessizlik kaldır" : "24 saat sessizleştir"}
                    onClick={e=>{e.stopPropagation();setMutedIsbns(m=>{const n={...m}; if(isMuted){delete n[isbn];}else{n[isbn]=Date.now()+86400000;} return n;});}}
                    style={{background:"none",border:`1px solid ${C.border}`,borderRadius:4,color:isMuted?C.orange:C.muted3,fontFamily:"var(--mono)",fontSize:10,padding:"1px 7px",cursor:"pointer"}}>
                    {isMuted?"🔔":"🔇"}
                  </button>
                </div>
              </div>
              {/* Expanded rows */}
              {isExpanded && (
                <div style={{border:`1px solid ${C.border}`,borderTop:"none",borderRadius:"0 0 8px 8px",overflow:"hidden"}}>
                  {rows.map((e,i) => (
                    <div key={e.item_id} style={{borderTop: i>0?`1px solid ${C.border}20`:"none"}}>
                      {renderRow(e,i)}
                    </div>
                  ))}
                </div>
              )}
              {/* Collapsed: show only best */}
              {!isExpanded && renderRow(best, 0)}
            </div>
          );
        });
      })()}

      {/* ─── Lightbox ─────────────────────────────────────────────────────────── */}
      {lightboxSrc && (
        <div onClick={()=>setLightboxSrc(null)} style={{
          position:"fixed",inset:0,zIndex:200,background:"rgba(0,0,0,.85)",
          display:"flex",alignItems:"center",justifyContent:"center",cursor:"zoom-out",
        }}>
          <img src={lightboxSrc} alt="" style={{maxWidth:"90vw",maxHeight:"90vh",objectFit:"contain",borderRadius:8,boxShadow:"0 8px 40px rgba(0,0,0,.6)"}}/>
          <button onClick={()=>setLightboxSrc(null)} style={{position:"absolute",top:20,right:24,background:"none",border:"none",color:"white",fontSize:28,cursor:"pointer",lineHeight:1}}>×</button>
        </div>
      )}

      {/* ─── Detail Drawer — shared DetailDrawer component ─────────────────── */}
      {selectedAlert && (
        <DetailDrawer
          isbn={selectedAlert.isbn}
          alertEntry={selectedAlert}
          drawerData={drawerData}
          drawerLoading={drawerLoading}
          soldScrape={soldScrape[selectedAlert.isbn]}
          bfScrape={bfData[selectedAlert.isbn]}
          bookMeta={bookMeta}
          C={C}
          onClose={()=>{setSelectedAlert(null);setDrawerData(null);}}
          onRetry={()=>openDrawer(selectedAlert)}
          onSoldFetch={(isbn)=>fetchSoldScrape(isbn)}
          onBfFetch={(isbn,cond)=>fetchBookfinder(isbn,cond||"all")}
          onLightbox={(src)=>setLightboxSrc(src)}
        />
      )}
    </div>
  );
}

// ── Drawer helpers ────────────────────────────────────────────────────────────
function DrawerSection({ title, children, C }) {
  return (
    <div style={{marginBottom:20}}>
      <div style={{fontSize:10,fontWeight:600,color:C.muted,marginBottom:8,letterSpacing:"0.04em",paddingBottom:4,borderBottom:`1px solid ${C.border}`}}>
        {title}
      </div>
      {children}
    </div>
  );
}
function DrawerRow({ label, value, C, valueColor }) {
  return (
    <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"3px 0",fontSize:11}}>
      <span style={{color:C.muted}}>{label}</span>
      <span style={{color:valueColor||C.text,fontWeight:500}}>{value}</span>
    </div>
  );
}
function AccordionSection({ title, children, C, defaultOpen=false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{marginBottom:12,border:`1px solid ${C.border}`,borderRadius:8,overflow:"hidden"}}>
      <button onClick={()=>setOpen(o=>!o)} style={{
        width:"100%",textAlign:"left",background:C.surface2,border:"none",
        padding:"9px 14px",cursor:"pointer",display:"flex",justifyContent:"space-between",alignItems:"center",
        color:C.text,fontSize:11,fontWeight:600,
      }}>
        <span>{title}</span>
        <span style={{color:C.muted3,fontSize:10,transition:"transform .2s",transform:open?"rotate(180deg)":"none"}}>▼</span>
      </button>
      {open && <div style={{padding:"12px 14px"}}>{children}</div>}
    </div>
  );
}


// ══════════════════════════════════════════════════════════════════════════════
// DetailDrawer — shared by AlertsFeedTab + WatchlistTab
// Props:
//   isbn         : string (required)
//   alertEntry   : object|null — if provided, shows hero KPIs (total/limit/score)
//   drawerData   : object|null — /alerts/details response
//   drawerLoading: bool
//   soldScrape   : object — { loading, data, error }
//   bfScrape     : object — { loading, data, error } (BookFinder)
//   bookMeta     : object
//   C            : colors
//   onClose      : fn
//   onRetry      : fn
//   onSoldFetch  : fn(isbn)
//   onBfFetch    : fn(isbn)
//   onLightbox   : fn(src)  (optional — pass null to disable)
// ══════════════════════════════════════════════════════════════════════════════
function DetailDrawer({
  isbn, alertEntry = null,
  drawerData, drawerLoading,
  soldScrape, bfScrape, bookMeta, C,
  onClose, onRetry, onSoldFetch, onBfFetch, onLightbox,
}) {
  const condLabel = { brand_new:"New", like_new:"Like New", very_good:"Very Good", good:"Good", acceptable:"Acceptable", used_all:"Used" };
  const condColor = (b) => ({brand_new:C.green,like_new:C.blue,very_good:C.purple,good:C.accent,acceptable:C.orange,used_all:C.muted})[b] || C.muted;
  const olCover = `https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg`;
  const coverSrc = alertEntry?.image_url || olCover;
  const meta = bookMeta?.[isbn] || {};

  return (
    <>
      {/* Backdrop */}
      <div onClick={onClose} style={{position:"fixed",inset:0,zIndex:40,background:"rgba(0,0,0,.45)",backdropFilter:"blur(2px)"}}/>

      {/* Panel */}
      <div style={{
        position:"fixed",top:0,right:0,bottom:0,zIndex:50,
        width:"60vw",minWidth:400,maxWidth:"95vw",background:C.surface,
        borderLeft:`1px solid ${C.border}`,display:"flex",flexDirection:"column",
        boxShadow:"-8px 0 32px rgba(0,0,0,.3)",overflow:"hidden",
      }}>

        {/* ── Hero ─────────────────────────────────────────────────────────── */}
        <div style={{padding:"16px 18px",borderBottom:`1px solid ${C.border}`,display:"flex",alignItems:"flex-start",gap:14,flexShrink:0}}>
          {/* Cover */}
          <div
            onClick={onLightbox ? ()=>onLightbox(coverSrc) : undefined}
            style={{width:110,height:155,flexShrink:0,borderRadius:8,overflow:"hidden",
              background:C.surface2,border:`1px solid ${C.border}`,
              cursor:onLightbox?"zoom-in":"default",
              display:"flex",alignItems:"center",justifyContent:"center",
            }}
          >
            <img src={coverSrc} alt="" loading="lazy"
              style={{width:"100%",height:"100%",objectFit:"contain"}}
              onError={e=>{ if(e.target.src!==olCover) e.target.src=olCover; }}
            />
          </div>

          {/* Title + meta + KPIs */}
          <div style={{flex:1,minWidth:0}}>
            <div style={{fontSize:13,fontWeight:700,color:C.text,lineHeight:1.4,marginBottom:3,wordBreak:"break-word"}}>
              {alertEntry?.title || meta.title || isbn}
            </div>
            <div style={{fontSize:10,color:C.muted3,fontFamily:"var(--mono)",marginBottom:10}}>
              <span>{isbn}</span>
              {meta.author && <span style={{marginLeft:6,color:C.muted}}>{meta.author}</span>}
              {meta.year   && <span style={{marginLeft:4,color:C.muted3}}> · {meta.year}</span>}
            </div>

            {/* Alert KPIs — only if alertEntry provided */}
            {alertEntry ? (
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7}}>
                {/* Total */}
                <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                  <div style={{fontSize:9,color:C.muted,marginBottom:2}}>📦 TOPLAM</div>
                  <div style={{fontSize:18,fontWeight:700,color:C.text}}>${alertEntry.total}</div>
                  {alertEntry.ship_estimated && <div style={{fontSize:9,color:C.orange}}>🚚 est.ship</div>}
                </div>
                {/* Limit */}
                <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                  <div style={{fontSize:9,color:C.muted,marginBottom:2}}>🎯 LİMİT</div>
                  <div style={{fontSize:18,fontWeight:700,color:alertEntry.total<=alertEntry.limit?C.green:C.red}}>
                    ${alertEntry.limit}
                  </div>
                  <div style={{fontSize:9,color:C.muted,marginTop:1}}>
                    {alertEntry.total<=alertEntry.limit
                      ? `✓ $${(alertEntry.limit-alertEntry.total).toFixed(2)} altında`
                      : `↑ $${(alertEntry.total-alertEntry.limit).toFixed(2)} üstünde`}
                  </div>
                </div>
                {/* Condition */}
                <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                  <div style={{fontSize:9,color:C.muted,marginBottom:2}}>🏷 KONDİSYON</div>
                  <div style={{fontSize:12,fontWeight:600,color:condColor(alertEntry.condition)}}>
                    {condLabel[alertEntry.condition]||alertEntry.condition}
                  </div>
                  <div style={{fontSize:9,color:C.muted,marginTop:1}}>
                    {alertEntry.match_quality==="CONFIRMED"?"✅ GTIN doğru":"⚠ unverified"}
                  </div>
                </div>
                {/* Score */}
                <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                  <div style={{fontSize:9,color:C.muted,marginBottom:2}}>🔥 SKOR</div>
                  <div style={{fontSize:18,fontWeight:700,color:alertEntry.deal_score>=75?C.green:alertEntry.deal_score>=50?C.accent:C.muted}}>
                    {alertEntry.deal_score!=null?alertEntry.deal_score:"—"}
                  </div>
                  <div style={{fontSize:9,color:C.muted,marginTop:1}}>
                    {alertEntry.decision==="OFFER"?"make offer":"fixed price"}
                  </div>
                </div>
              </div>
            ) : (
              /* Watchlist mode: show active eBay KPIs if data already loaded */
              drawerData?.ebay?.ok && (
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:7}}>
                  {drawerData.ebay.used && (
                    <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:2}}>🧺 USED MIN</div>
                      <div style={{fontSize:18,fontWeight:700,color:C.accent}}>${drawerData.ebay.used.min}</div>
                      <div style={{fontSize:9,color:C.muted3}}>{drawerData.ebay.used.count} ilan · ort ${drawerData.ebay.used.avg}</div>
                    </div>
                  )}
                  {drawerData.ebay.new && (
                    <div style={{background:C.surface2,borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:2}}>🆕 NEW MIN</div>
                      <div style={{fontSize:18,fontWeight:700,color:C.green}}>${drawerData.ebay.new.min}</div>
                      <div style={{fontSize:9,color:C.muted3}}>{drawerData.ebay.new.count} ilan</div>
                    </div>
                  )}
                  {drawerData.amazon?.available && drawerData.amazon?.used?.buybox && (
                    <div style={{background:C.surface2,border:`1px solid ${C.accent}44`,borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:C.accent,marginBottom:2,fontWeight:700}}>🛒 BB USED</div>
                      <div style={{fontSize:18,fontWeight:700,color:C.accent}}>${drawerData.amazon.used.buybox.total_int ?? drawerData.amazon.used.buybox.total}</div>
                      <div style={{fontSize:9,color:C.muted2}}>{drawerData.amazon.used.buybox.label==="A"?"FBA":"FBM"} · Amazon</div>
                    </div>
                  )}
                  {drawerData.amazon?.available && drawerData.amazon?.new?.buybox && (
                    <div style={{background:C.surface2,border:`1px solid ${C.green}44`,borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:C.green,marginBottom:2,fontWeight:700}}>🛒 BB NEW</div>
                      <div style={{fontSize:18,fontWeight:700,color:C.green}}>${drawerData.amazon.new.buybox.total_int ?? drawerData.amazon.new.buybox.total}</div>
                      <div style={{fontSize:9,color:C.muted2}}>{drawerData.amazon.new.buybox.label==="A"?"FBA":"FBM"} · Amazon</div>
                    </div>
                  )}
                </div>
              )
            )}
          </div>

          <button onClick={onClose} style={{background:"none",border:"none",color:C.muted,cursor:"pointer",fontSize:20,lineHeight:1,padding:4,flexShrink:0,marginTop:-4}}>×</button>
        </div>

        {/* ── Body ─────────────────────────────────────────────────────────── */}
        <div style={{flex:1,overflowY:"auto",padding:"14px 18px"}}>

          {drawerLoading && (
            <div style={{color:C.muted3,fontSize:12,textAlign:"center",paddingTop:32}}>Yükleniyor…</div>
          )}

          {!drawerLoading && drawerData && !drawerData.ok && (
            <div style={{padding:"20px 0",textAlign:"center"}}>
              <div style={{fontSize:24,marginBottom:8}}>⚠️</div>
              <div style={{fontSize:12,color:C.orange,marginBottom:12}}>{drawerData.error||"Veri yüklenemedi"}</div>
              <button onClick={onRetry} style={{fontSize:11,background:"none",border:`1px solid ${C.border}`,borderRadius:5,color:C.muted,padding:"6px 14px",cursor:"pointer"}}>↺ Tekrar dene</button>
            </div>
          )}

          {!drawerLoading && drawerData?.ok && (
            <>

              {/* ── eBay Aktif Listeler ──────────────────────────────────── */}
              <AccordionSection title="📊 eBay Aktif Listeler" C={C} defaultOpen={true}>
                {drawerData.ebay?.ok ? (
                  <>
                    {drawerData.ebay.stale && (
                      <div style={{fontSize:9,color:C.orange,background:C.orange+"11",border:"1px solid #f59e0b44",borderRadius:4,padding:"4px 8px",marginBottom:6}}>
                        ⚠ Cache verisi ({drawerData.ebay.stale_age_h}s önce) — eBay bot koruması aktifti
                      </div>
                    )}
                    <div style={{display:"flex",gap:14,flexWrap:"wrap",fontSize:11,marginBottom:10,padding:"8px 10px",background:C.surface2,borderRadius:6}}>
                      {drawerData.ebay.used && <>
                        <span>🧺 <b style={{color:C.accent}}>{drawerData.ebay.used.count}</b> used</span>
                        <span>💸 min <b style={{color:C.accent}}>${drawerData.ebay.used.min}</b></span>
                        <span>📈 avg <b style={{color:C.muted}}>${drawerData.ebay.used.avg}</b></span>
                      </>}
                      {drawerData.ebay.new && <>
                        <span>🆕 <b style={{color:C.green}}>{drawerData.ebay.new.count}</b> new</span>
                        <span>💸 min <b style={{color:C.green}}>${drawerData.ebay.new.min}</b></span>
                      </>}
                    </div>
                    {drawerData.ebay.by_condition && Object.keys(drawerData.ebay.by_condition).length>0 && (
                      <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                        <thead>
                          <tr style={{borderBottom:`1px solid ${C.border}`}}>
                            {["Kondisyon","Adet","Min","Ort"].map(h=>(
                              <th key={h} style={{textAlign:h==="Kondisyon"?"left":"right",padding:"3px 6px",fontSize:9,color:C.muted,fontWeight:500}}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(drawerData.ebay.by_condition)
                            .sort((a,b)=>a[1].min-b[1].min)
                            .map(([cond,st])=>(
                              <tr key={cond} style={{borderBottom:`1px solid ${C.border}10`}}>
                                <td style={{padding:"5px 6px",color:condColor(cond),fontWeight:500}}>{condLabel[cond]||cond}</td>
                                <td style={{padding:"5px 6px",textAlign:"right",color:C.muted}}>{st.count}</td>
                                <td style={{padding:"5px 6px",textAlign:"right",color:C.text,fontWeight:600}}>${st.min}</td>
                                <td style={{padding:"5px 6px",textAlign:"right",color:C.muted}}>${st.avg}</td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    )}
                  </>
                ) : (
                  <div style={{fontSize:11,color:C.muted3}}>{drawerData.ebay?.error||"Veri alınamadı"}</div>
                )}
              </AccordionSection>

              {/* ── Profit Simülasyonu ───────────────────────────────────── */}
              {drawerData.profit && (
                <AccordionSection title="💰 Kâr Simülasyonu" C={C} defaultOpen={true}>
                  {(()=>{
                    const p = drawerData.profit;
                    const tierEmoji = {fire:"🔥",good:"👍",low:"😬",loss:"❌"}[p.roi_tier]||"";
                    const profitColor = p.profit>0?C.green:C.red;
                    const roiColor = p.roi_pct>=30?C.green:p.roi_pct>=15?C.accent:p.roi_pct>0?C.orange:C.red;
                    return (
                      <>
                        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:12}}>
                          <div style={{background:C.surface2,borderRadius:7,padding:"10px 14px",border:`1px solid ${p.profit>0?C.green:C.red}20`}}>
                            <div style={{fontSize:9,color:C.muted,marginBottom:3}}>✅ NET KÂR</div>
                            <div style={{fontSize:22,fontWeight:700,color:profitColor}}>{p.profit>0?"+":""}${Math.abs(p.profit).toFixed(2)}</div>
                          </div>
                          <div style={{background:C.surface2,borderRadius:7,padding:"10px 14px"}}>
                            <div style={{fontSize:9,color:C.muted,marginBottom:3}}>📈 ROI</div>
                            <div style={{fontSize:22,fontWeight:700,color:roiColor}}>{p.roi_pct>0?"+":""}{p.roi_pct}%</div>
                            <div style={{fontSize:10,color:roiColor,marginTop:2}}>{tierEmoji} {p.roi_tier}</div>
                          </div>
                        </div>
                        <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                          <tbody>
                            {[
                              ["🛒 Amazon sell price",`$${p.sell_price}`,C.text,`(${p.sell_source.replace(/_/g," ")})`],
                              ["📦 eBay cost",`-$${p.ebay_cost}`,C.red,""],
                              ["💸 Referral (15%)",`-$${p.referral_fee}`,C.muted,""],
                              ["📦 Closing fee",`-$${p.closing_fee}`,C.muted,"media"],
                              ["🚚 Fulfillment",`-$${p.fulfillment}`,C.muted,"FBA avg"],
                              ["✈ Inbound",`-$${p.inbound}`,C.muted,"estimate"],
                            ].map(([lbl,val,col,sub])=>(
                              <tr key={lbl} style={{borderBottom:`1px solid ${C.border}10`}}>
                                <td style={{padding:"4px 0",color:C.muted,fontSize:10}}>{lbl}</td>
                                <td style={{padding:"4px 4px",color:C.muted3,fontSize:9}}>{sub}</td>
                                <td style={{padding:"4px 0",textAlign:"right",color:col,fontWeight:500,fontFamily:"var(--mono)"}}>{val}</td>
                              </tr>
                            ))}
                            <tr style={{borderTop:`1px solid ${C.border}`}}>
                              <td colSpan={2} style={{padding:"6px 0",color:C.text,fontWeight:600,fontSize:11}}>Net</td>
                              <td style={{padding:"6px 0",textAlign:"right",color:profitColor,fontWeight:700,fontFamily:"var(--mono)"}}>{p.profit>0?"+":""}{p.profit}</td>
                            </tr>
                          </tbody>
                        </table>
                        <div style={{fontSize:9,color:C.muted3,marginTop:6}}>* Tahminler varsayıma dayanır. Gerçek FBA fee asin/weight bazlı değişir.</div>
                      </>
                    );
                  })()}
                </AccordionSection>
              )}

              {/* ── Satış Verisi ─────────────────────────────────────────── */}
              <AccordionSection title="📉 Satış Verisi" C={C} defaultOpen={false}>
                {/* On-demand sold scrape — new/used split */}
                {(()=>{
                  const ss = soldScrape;
                  return (
                    <div style={{marginTop:12,paddingTop:10,borderTop:`1px solid ${C.border}`}}>
                      {!ss?.data && !ss?.loading && (
                        <button
                          onClick={()=>onSoldFetch(isbn)}
                          style={{width:"100%",padding:"7px",borderRadius:6,fontSize:11,fontWeight:600,
                            background:"none",border:`1px solid ${C.accent}`,color:C.accent,
                            cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",gap:6}}
                        >
                          🔍 Satış Ortalaması Gör
                          <span style={{fontSize:9,color:C.muted3,fontWeight:400}}>(New + Used · on-demand)</span>
                        </button>
                      )}
                      {ss?.loading && <div style={{textAlign:"center",fontSize:11,color:C.muted3,padding:"8px 0"}}>⏳ eBay sold listesi çekiliyor…</div>}
                      {ss?.error && (
                        <div style={{fontSize:11,color:C.orange,display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                          <span>⚠ {ss.error}</span>
                          <button onClick={()=>onSoldFetch(isbn)} style={{fontSize:10,background:"none",border:"none",color:C.accent,cursor:"pointer"}}>↺</button>
                        </div>
                      )}
                      {ss?.data && !ss.data.ok && (
                        <div style={{background:C.surface2,borderRadius:8,padding:"12px",marginTop:4}}>
                          <div style={{fontSize:11,color:C.orange,fontWeight:600,marginBottom:6}}>
                            ⚠ {ss.data.ebay_blocked ? "eBay bot koruması aktif" : "Veri alınamadı"}
                          </div>
                          <div style={{fontSize:10,color:C.muted3,lineHeight:1.5}}>
                            {ss.data.ebay_blocked
                              ? "eBay sunucu isteklerini CAPTCHA ile engelliyor. Alternatif olarak BookFinder fiyat karşılaştırmasını kullanabilirsiniz."
                              : (ss.data.error || "Bilinmeyen hata")}
                          </div>
                          <div style={{display:"flex",gap:8,marginTop:8}}>
                            <button onClick={()=>onSoldFetch(isbn)} style={{fontSize:10,padding:"4px 10px",borderRadius:4,background:"none",border:`1px solid ${C.accent}`,color:C.accent,cursor:"pointer"}}>↺ Tekrar Dene</button>
                            <a href={ss.data.ebay_url_used||`https://www.ebay.com/sch/i.html?_nkw=${isbn}&LH_Sold=1&LH_Complete=1`}
                              target="_blank" rel="noreferrer"
                              style={{fontSize:10,padding:"4px 10px",borderRadius:4,background:"none",border:`1px solid ${C.muted3}`,color:C.muted3,textDecoration:"none",cursor:"pointer"}}>
                              eBay'de Aç ↗
                            </a>
                          </div>
                        </div>
                      )}
                      {ss?.data?.ok && (
                        <div>
                          {/* New + Used side by side */}
                          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:8}}>
                            {[["🆕 New",ss.data.new,C.green,ss.data.ebay_url_new],
                              ["🧺 Used",ss.data.used,C.accent,ss.data.ebay_url_used]
                            ].map(([label,st,col,url])=>(
                              <div key={label} style={{background:C.surface2,borderRadius:6,padding:"8px 10px"}}>
                                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                                  <span style={{fontSize:10,color:col,fontWeight:700}}>{label}</span>
                                  {st
                                    ? <span style={{fontSize:9,color:C.muted3}}>{st.count} satış</span>
                                    : <span style={{fontSize:9,color:C.muted3}}>veri yok</span>}
                                </div>
                                {st ? (
                                  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:3}}>
                                    {[["Ort",st.avg],[" Med",st.median],["Min",st.min],["Max",st.max]].map(([k,v])=>(
                                      <div key={k} style={{textAlign:"center"}}>
                                        <div style={{fontSize:7,color:C.muted3}}>{k}</div>
                                        <div style={{fontSize:12,fontWeight:700,color:k==="Min"?col:C.text,fontFamily:"var(--mono)"}}>${v}</div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div style={{fontSize:10,color:C.muted3,textAlign:"center"}}>—</div>
                                )}
                                <a href={url} target="_blank" rel="noreferrer"
                                  style={{display:"block",textAlign:"center",marginTop:6,fontSize:9,color:C.muted3,textDecoration:"none"}}>eBay ↗</a>
                              </div>
                            ))}
                          </div>
                          {/* Stale warning */}
                          {ss.data.stale && (
                            <div style={{fontSize:9,color:C.orange,background:C.orange+"11",border:"1px solid "+C.orange+"33",borderRadius:4,padding:"4px 8px",marginBottom:6}}>
                              ⚠ Eski veri ({ss.data.cache_date}) — eBay bot engeli nedeniyle güncel çekilemedi
                            </div>
                          )}
                          {/* Combined */}
                          {ss.data.combined && (
                            <div style={{fontSize:10,color:C.muted3,textAlign:"center",padding:"4px 0"}}>
                              Toplam {ss.data.combined.count} satış · ort <b style={{color:C.text}}>${ss.data.combined.avg}</b>
                              {ss.data.cached && <span style={{marginLeft:6}}>⚡ {ss.data.cache_date||"cache"}</span>}
                            </div>
                          )}
                          <button onClick={()=>onSoldFetch(isbn)} style={{display:"block",width:"100%",marginTop:6,fontSize:9,background:"none",border:"none",color:C.muted3,cursor:"pointer",textAlign:"center"}}>↺ Yenile</button>
                        </div>
                      )}
                      {ss?.data?.ok && !ss.data.new && !ss.data.used && (
                        <div style={{fontSize:11,color:C.muted3,textAlign:"center",padding:"6px 0"}}>Satış kaydı bulunamadı.</div>
                      )}
                    </div>
                  );
                })()}
              </AccordionSection>

              {/* ── Amazon BuyBox ────────────────────────────────────── */}
              {drawerData.amazon && (
                <AccordionSection title="🛒 Amazon BuyBox" C={C} defaultOpen={true}>
                  {drawerData.amazon.available ? (() => {
                    const az = drawerData.amazon;
                    const bbNew  = az.new?.buybox;
                    const bbUsed = az.used?.buybox;
                    const top2New  = az.new?.top2  || [];
                    const top2Used = az.used?.top2 || [];
                    const fmtLabel = l => l==="A" ? "FBA" : l==="M" ? "FBM" : l;
                    const PriceRow = ({label, bb, top2, color}) => (
                      <div style={{marginBottom:12,background:C.surface2,borderRadius:8,padding:"10px 12px",border:`1px solid ${color}33`}}>
                        <div style={{fontSize:9,fontWeight:700,color:color,textTransform:"uppercase",letterSpacing:1,marginBottom:6,opacity:.8}}>{label}</div>
                        {bb ? (
                          <div style={{display:"flex",alignItems:"baseline",gap:10,marginBottom:6}}>
                            <span style={{fontSize:28,fontWeight:900,color,lineHeight:1}}>${bb.total_int ?? bb.total}</span>
                            <span style={{fontSize:10,background:color+"22",color,padding:"3px 8px",borderRadius:4,fontWeight:700}}>
                              BuyBox · {fmtLabel(bb.label)}
                            </span>
                          </div>
                        ) : (
                          <div style={{fontSize:11,color:C.muted3,marginBottom:4}}>BuyBox yok</div>
                        )}
                        {top2.length > 0 && (
                          <div style={{display:"flex",gap:5,flexWrap:"wrap"}}>
                            {top2.map((o,i)=>(
                              <span key={i} style={{fontSize:10,background:C.bg||C.surface,padding:"2px 8px",borderRadius:4,color:C.muted,border:`1px solid ${C.border}`}}>
                                #{i+1} <b style={{color:C.text}}>${o.total_int ?? o.total}</b> <span style={{opacity:.6}}>{fmtLabel(o.label)}</span>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                    return (
                      <div>
                        <PriceRow label="New" bb={bbNew}  top2={top2New}  color={C.green||"#22c55e"} />
                        <PriceRow label="Used" bb={bbUsed} top2={top2Used} color={C.accent||"#3b82f6"} />
                        <div style={{fontSize:9,color:C.muted3,marginTop:4}}>ASIN: {az.asin} · SP-API anlık</div>
                      </div>
                    );
                  })() : (
                    <div style={{fontSize:11,color:C.muted3}}>
                      {drawerData.amazon.reason === "not_configured"
                        ? "Amazon SP-API yapılandırılmamış (.env'de LWA_* eksik)"
                        : drawerData.amazon.note?.includes("401")
                          ? "⚠️ Amazon credentials geçersiz — LWA_REFRESH_TOKEN süresi dolmuş olabilir"
                          : drawerData.amazon.note || drawerData.amazon.reason || "Veri yok"}
                    </div>
                  )}
                </AccordionSection>
              )}

              {/* ── Keepa Fiyat Geçmişi Grafiği ─────────────────────── */}
              <AccordionSection title="📈 Fiyat Geçmişi (Keepa)" C={C} defaultOpen={true}>
                {/* BuyBox anlık fiyat — SP-API verisinden, grafik üstüne çizgi + badge */}
                {drawerData.amazon?.available && (() => {
                  const az = drawerData.amazon;
                  const bbNew  = az.new?.buybox;
                  const bbUsed = az.used?.buybox;
                  const fmtLabel = l => l==="A"?"FBA":l==="M"?"FBM":l||"";
                  if (!bbNew && !bbUsed) return null;
                  return (
                    <div style={{marginBottom:8}}>
                      {/* Badge satırı */}
                      <div style={{display:"flex",gap:6,marginBottom:6,flexWrap:"wrap"}}>
                        {bbNew && (
                          <div style={{flex:1,minWidth:110,background:C.surface2,border:`1px solid ${C.green||"#22c55e"}55`,borderRadius:6,padding:"7px 10px",display:"flex",alignItems:"baseline",gap:7}}>
                            <span style={{fontSize:9,color:C.green||"#22c55e",fontWeight:700,textTransform:"uppercase",letterSpacing:.5,whiteSpace:"nowrap"}}>BB New</span>
                            <span style={{fontSize:20,fontWeight:900,color:C.green||"#22c55e",lineHeight:1}}>${bbNew.total_int ?? bbNew.total}</span>
                            <span style={{fontSize:9,color:C.muted2,marginLeft:"auto"}}>{fmtLabel(bbNew.label)}</span>
                          </div>
                        )}
                        {bbUsed && (
                          <div style={{flex:1,minWidth:110,background:C.surface2,border:`1px solid ${C.accent||"#f0a500"}55`,borderRadius:6,padding:"7px 10px",display:"flex",alignItems:"baseline",gap:7}}>
                            <span style={{fontSize:9,color:C.accent||"#f0a500",fontWeight:700,textTransform:"uppercase",letterSpacing:.5,whiteSpace:"nowrap"}}>BB Used</span>
                            <span style={{fontSize:20,fontWeight:900,color:C.accent||"#f0a500",lineHeight:1}}>${bbUsed.total_int ?? bbUsed.total}</span>
                            <span style={{fontSize:9,color:C.muted2,marginLeft:"auto"}}>{fmtLabel(bbUsed.label)}</span>
                          </div>
                        )}
                      </div>
                      {/* Grafik üstüne fiyat çizgisi görseli */}
                      <div style={{position:"relative",marginBottom:2}}>
                        {/* Pseudo çizgiler — grafik yüklendikten sonra overlay olarak */}
                        {bbNew && (
                          <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:3}}>
                            <div style={{flex:1,height:2,background:`linear-gradient(90deg, ${C.green||"#22c55e"}cc, transparent)`,borderRadius:2}}/>
                            <span style={{fontSize:8,color:C.green||"#22c55e",fontWeight:700,whiteSpace:"nowrap"}}>▶ BB New ${bbNew.total_int ?? bbNew.total}</span>
                          </div>
                        )}
                        {bbUsed && (
                          <div style={{display:"flex",alignItems:"center",gap:6}}>
                            <div style={{flex:1,height:2,background:`linear-gradient(90deg, ${C.accent||"#f0a500"}cc, transparent)`,borderRadius:2}}/>
                            <span style={{fontSize:8,color:C.accent||"#f0a500",fontWeight:700,whiteSpace:"nowrap"}}>▶ BB Used ${bbUsed.total_int ?? bbUsed.total}</span>
                          </div>
                        )}
                      </div>
                      <div style={{fontSize:8,color:C.muted3}}>⚡ SP-API anlık · Keepa Pro olmadan grafik çizgisi gösterilemiyor</div>
                    </div>
                  );
                })()}
                <div style={{position:"relative",background:C.surface2,borderRadius:8,overflow:"hidden",marginBottom:8}}>
                  {/* Loading placeholder */}
                  <div id={`keepa-loading-${isbn}`} style={{textAlign:"center",padding:"20px 0",fontSize:11,color:C.muted3}}>
                    ⏳ Keepa grafiği yükleniyor…
                  </div>
                  <a href={`https://keepa.com/#!search/1-${isbn13to10(isbn)}`} target="_blank" rel="noreferrer"
                    style={{display:"block"}}>
                    <img
                      src={`https://graph.keepa.com/pricehistory.png?asin=${isbn13to10(isbn)}&domain=com&range=180&new=1&used=1&salesrank=1&width=500&height=200`}
                      alt="Keepa Price History"
                      style={{width:"100%",height:"auto",display:"none",borderRadius:8,minHeight:80,background:C.surface2}}
                      onLoad={e=>{
                        e.target.style.display="block";
                        const loader=document.getElementById(`keepa-loading-${isbn}`);
                        if(loader)loader.style.display="none";
                      }}
                      onError={e=>{
                        e.target.style.display="none";
                        const loader=document.getElementById(`keepa-loading-${isbn}`);
                        if(loader)loader.innerHTML='<span style="color:#f59e0b">Keepa grafiği yüklenemedi</span> · <a href="https://keepa.com/#!search/1-'+(isbn13to10(isbn))+'" target="_blank" rel="noreferrer" style="color:#3b82f6">Keepa\'da aç ↗</a>';
                      }}
                    />
                  </a>
                </div>
                <div style={{display:"flex",gap:8,justifyContent:"center",flexWrap:"wrap",marginTop:4}}>
                  <a href={`https://keepa.com/#!search/1-${isbn13to10(isbn)}`} target="_blank" rel="noreferrer"
                    style={{flex:1,fontSize:11,color:"white",textDecoration:"none",padding:"8px 12px",
                      background:"#2563eb",borderRadius:6,textAlign:"center",fontWeight:600,
                      display:"block",boxShadow:"0 2px 6px rgba(37,99,235,.3)"}}>
                    🐝 Keepa Detay ↗
                  </a>
                  <a href={`https://camelcamelcamel.com/product/${isbn}`} target="_blank" rel="noreferrer"
                    style={{flex:1,fontSize:11,color:"white",textDecoration:"none",padding:"8px 12px",
                      background:"#059669",borderRadius:6,textAlign:"center",fontWeight:600,
                      display:"block",boxShadow:"0 2px 6px rgba(5,150,105,.3)"}}>
                    📈 CamelCamelCamel ↗
                  </a>
                </div>
              </AccordionSection>

              {/* ── En Ucuz Fiyat Bul — 8 Kaynak ──────────────────────── */}
              <AccordionSection title="📚 En Ucuz Fiyat Bul" C={C} defaultOpen={true}>
                {(()=>{
                  const bf = bfScrape;
                  const isLoading = bf?.loading;
                  const activeCond = bf?.condition || "all";
                  const doFetch = (cond) => { if(onBfFetch) onBfFetch(isbn, cond); };
                  const SRC = {bookfinder:"📚 BookFinder",abebooks:"📖 AbeBooks",thriftbooks:"♻️ ThriftBooks",
                               bwb:"🌍 BetterWorldBooks",biblio:"📗 Biblio",alibris:"📕 Alibris",
                               goodwill:"💛 GoodwillBooks",hpb:"🔴 HPB"};
                  return (
                    <div>
                      {/* Butonlar */}
                      {!isLoading && (
                        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginBottom:8}}>
                          <button onClick={()=>doFetch("used")} style={{padding:"9px 4px",borderRadius:6,fontSize:11,fontWeight:700,
                            background:activeCond==="used"&&bf?.data?.ok?C.accent||"#3b82f6":C.surface2,
                            color:activeCond==="used"&&bf?.data?.ok?"white":C.accent||"#3b82f6",
                            border:`1.5px solid ${C.accent||"#3b82f6"}`,cursor:"pointer"}}>
                            🧺 En Ucuz Used
                          </button>
                          <button onClick={()=>doFetch("new")} style={{padding:"9px 4px",borderRadius:6,fontSize:11,fontWeight:700,
                            background:activeCond==="new"&&bf?.data?.ok?C.green||"#22c55e":C.surface2,
                            color:activeCond==="new"&&bf?.data?.ok?"white":C.green||"#22c55e",
                            border:`1.5px solid ${C.green||"#22c55e"}`,cursor:"pointer"}}>
                            🆕 En Ucuz New
                          </button>
                          <button onClick={()=>doFetch("all")} style={{gridColumn:"1/-1",padding:"6px",borderRadius:5,fontSize:10,fontWeight:600,
                            background:C.surface2,color:C.muted,border:`1px solid ${C.border}`,cursor:"pointer"}}>
                            📊 Tümünü Getir (New + Used)
                          </button>
                        </div>
                      )}
                      {/* Loading */}
                      {isLoading && (
                        <div style={{textAlign:"center",padding:"16px 0"}}>
                          <div style={{fontSize:12,fontWeight:700,color:C.purple||"#7c3aed",marginBottom:6}}>
                            ⏳ Fiyatlar çekiliyor…
                          </div>
                          <div style={{fontSize:9,color:C.muted3,lineHeight:1.6}}>AbeBooks · ThriftBooks · BetterWorldBooks<br/>Biblio · Alibris · GoodwillBooks · HPB · BookFinder</div>
                          <div style={{fontSize:9,color:C.muted3,marginTop:4}}>8 kaynak paralel · 10-20 saniye</div>
                        </div>
                      )}
                      {/* Hata */}
                      {!isLoading && bf?.data && !bf.data.ok && (
                        <div style={{background:C.surface2,borderRadius:6,padding:"10px 12px"}}>
                          <div style={{fontSize:11,color:C.orange,marginBottom:6}}>⚠ {bf.data.error}</div>
                          <button onClick={()=>doFetch(activeCond)} style={{fontSize:10,padding:"4px 12px",borderRadius:4,background:"none",border:`1px solid ${C.accent}`,color:C.accent,cursor:"pointer"}}>↺ Tekrar Dene</button>
                        </div>
                      )}
                      {/* Sonuçlar */}
                      {!isLoading && bf?.data?.ok && (()=>{
                        const d = bf.data;
                        const allOffers = [
                          ...(d.used?.offers||[]).map(o=>({...o,_new:false})),
                          ...(d.new?.offers||[]).map(o=>({...o,_new:true})),
                        ].sort((a,b)=>a.total-b.total);
                        const cheapest = allOffers[0];
                        return (
                          <div>
                            {/* En ucuz highlight */}
                            {cheapest && (()=>{
                              const chCol = cheapest._new?(C.green||"#22c55e"):(C.accent||"#3b82f6");
                              const chSrcKey = Object.entries(d.source_labels||{}).find(([k,v])=>v.includes(cheapest.seller)||cheapest.seller.includes(v?.replace(/^[^\s]+\s/,"")))?.[0];
                              const chUrl = chSrcKey ? (d.source_urls||{})[chSrcKey] : null;
                              return (
                                <div style={{background:C.surface2,border:`1.5px solid ${chCol}66`,
                                  borderRadius:8,padding:"10px 14px",marginBottom:10,
                                  display:"flex",alignItems:"center",gap:10}}>
                                  <div>
                                    <div style={{fontSize:9,color:chCol,fontWeight:700,letterSpacing:.5,opacity:.8}}>
                                      🏆 EN UCUZ {cheapest._new?"NEW":"USED"}
                                    </div>
                                    <div style={{fontSize:24,fontWeight:900,color:chCol,lineHeight:1.1}}>
                                      ${cheapest.total}
                                    </div>
                                    {cheapest.shipping>0&&<div style={{fontSize:9,color:C.muted2}}>${cheapest.price} + ${cheapest.shipping} kargo</div>}
                                  </div>
                                  <div style={{marginLeft:"auto",textAlign:"right"}}>
                                    <div style={{fontSize:12,fontWeight:700,color:C.text}}>
                                      {chUrl
                                        ? <a href={chUrl} target="_blank" rel="noreferrer" style={{color:C.text,textDecoration:"none"}}>{cheapest.seller} ↗</a>
                                        : cheapest.seller}
                                    </div>
                                    <div style={{fontSize:9,color:C.muted2}}>{cheapest._new?"New":"Used"}</div>
                                  </div>
                                </div>
                              );
                            })()}
                            {/* Özet kartlar */}
                            <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:6,marginBottom:8}}>
                              {[[d.used,"🧺 Used",C.accent||"#3b82f6"],[d.new,"🆕 New",C.green||"#22c55e"]].map(([st,lbl,col])=>st?(
                                <div key={lbl} style={{background:C.surface2,borderRadius:6,padding:"8px 10px"}}>
                                  <div style={{display:"flex",justifyContent:"space-between",marginBottom:3}}>
                                    <span style={{fontSize:10,color:col,fontWeight:700}}>{lbl}</span>
                                    <span style={{fontSize:9,color:C.muted2}}>{st.count} ilan</span>
                                  </div>
                                  <div style={{fontSize:17,fontWeight:800,color:col}}>${st.min}</div>
                                  <div style={{fontSize:9,color:C.muted2}}>ort ${st.avg}</div>
                                </div>
                              ):null)}
                            </div>
                            {/* Tam ilan tablosu */}
                            <div style={{fontSize:9,color:C.muted,fontWeight:700,marginBottom:4,letterSpacing:.5,textTransform:"uppercase"}}>
                              Tüm İlanlar ({allOffers.length})
                            </div>
                            <table style={{width:"100%",borderCollapse:"collapse",fontSize:10}}>
                              <thead>
                                <tr style={{borderBottom:`1px solid ${C.border}`}}>
                                  {["Satıcı","Tür","Fiyat","Kargo","Toplam"].map(h=>(
                                    <th key={h} style={{textAlign:h==="Satıcı"||h==="Tür"?"left":"right",padding:"3px 4px",fontSize:8,color:C.muted,fontWeight:600}}>{h}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {allOffers.slice(0,20).map((o,i)=>{
                                  const srcKey = Object.entries(d.source_labels||{}).find(([k,v])=>v.includes(o.seller)||o.seller.includes(v?.replace(/^[^\s]+\s/,"")))?.[0];
                                  const srcUrl = srcKey ? (d.source_urls||{})[srcKey] : null;
                                  return (
                                    <tr key={i} style={{borderBottom:`1px solid ${C.border}`,background:i===0?C.surface2:""}}>
                                      <td style={{padding:"4px",color:C.text,maxWidth:90,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",fontWeight:i===0?700:400}}>
                                        {i===0&&"🏆 "}
                                        {srcUrl
                                          ? <a href={srcUrl} target="_blank" rel="noreferrer" style={{color:C.text,textDecoration:"none",borderBottom:`1px dotted ${C.muted3}`}}>{o.seller}</a>
                                          : o.seller}
                                      </td>
                                      <td style={{padding:"4px",fontSize:9,color:o._new?(C.green||"#22c55e"):(C.accent||"#3b82f6")}}>{o._new?"New":"Used"}</td>
                                      <td style={{padding:"4px",textAlign:"right",color:C.muted,fontFamily:"var(--mono)"}}>${o.price}</td>
                                      <td style={{padding:"4px",textAlign:"right",color:C.muted2,fontFamily:"var(--mono)"}}>{o.shipping>0?`$${o.shipping}`:"free"}</td>
                                      <td style={{padding:"4px",textAlign:"right",fontWeight:700,fontFamily:"var(--mono)",
                                        color:i===0?(o._new?(C.green||"#22c55e"):(C.accent||"#3b82f6")):C.text}}>${o.total}</td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                            {/* Kaynaklar + meta */}
                            <div style={{marginTop:8,display:"flex",gap:3,flexWrap:"wrap",alignItems:"center"}}>
                              {(d.sources||[]).map(s=>(
                                <a key={s} href={(d.source_urls||{})[s]||"#"} target="_blank" rel="noreferrer"
                                  style={{fontSize:8,background:C.surface2,border:`1px solid ${C.border}`,borderRadius:3,padding:"1px 5px",color:C.muted,textDecoration:"none"}}>
                                  {SRC[s]||s}
                                </a>
                              ))}
                              <span style={{marginLeft:"auto",fontSize:8,color:C.muted2}}>
                                {d.cached&&"⚡ cache · "}{d.total_offers} ilan
                              </span>
                            </div>
                            <div style={{display:"flex",justifyContent:"space-between",marginTop:5}}>
                              <a href={d.bookfinder_url} target="_blank" rel="noreferrer" style={{fontSize:9,color:C.muted2,textDecoration:"none"}}>BookFinder ↗</a>
                              <button onClick={()=>doFetch(activeCond)} style={{fontSize:9,background:"none",border:"none",color:C.muted2,cursor:"pointer"}}>↺ Yenile</button>
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  );
                })()}
              </AccordionSection>

              {/* ── 📊 Score Analizi — only for alert entries ────────────────── */}
              {alertEntry?.deal_score != null && (
                <AccordionSection title={`🧮 📊 Score Analizi · ${alertEntry.deal_score}/100`} C={C} defaultOpen={false}>
                  {(()=>{
                    const s = alertEntry;
                    const condLabel2 = { brand_new:"New", like_new:"Like New", very_good:"Very Good", good:"Good", acceptable:"Acceptable", used_all:"Used" };
                    const ratioRaw  = s.limit>0 ? Math.max(0,(1-s.total/s.limit))*70 : 0;
                    const condBonus = {brand_new:8,like_new:8,very_good:5,good:0,acceptable:-5,used_all:0}[s.condition]??0;
                    const offerBonus = s.decision==="OFFER"?10:0;
                    const shipPenalty = s.ship_estimated?-2:0;
                    const soldPenalty = (s.sold_avg!=null&&s.sold_avg<s.total)?-5:0;
                    const rows=[
                      ["🎯 Limit'e uzaklık",`+${Math.round(ratioRaw)}`,C.green,`${s.total} / ${s.limit}`],
                      ["🏷 Kondisyon",condBonus>=0?`+${condBonus}`:String(condBonus),condBonus>=0?C.green:C.orange,condLabel2[s.condition]||s.condition],
                      ["💼 Make Offer",offerBonus?"+10":"0",offerBonus?C.blue:C.muted3,offerBonus?"OFFER modu":"—"],
                      ["🚚 Est. shipping",shipPenalty?String(shipPenalty):"0",shipPenalty?C.orange:C.muted3,shipPenalty?"tahmini":"sabit"],
                      ["📉 Sold avg üstü",soldPenalty?"-5":"0",soldPenalty?C.red:C.muted3,soldPenalty?`sold $${Math.round(s.sold_avg)} < buy $${s.total}`:s.sold_avg==null?"veri yok — ceza yok":"OK (sold ≥ buy)"],
                    ];
                    return (
                      <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                        <tbody>
                          {rows.map(([lbl,val,col,note])=>(
                            <tr key={lbl} style={{borderBottom:`1px solid ${C.border}10`}}>
                              <td style={{padding:"5px 0",color:C.muted}}>{lbl}</td>
                              <td style={{padding:"5px 4px",color:C.muted3,fontSize:9}}>{note}</td>
                              <td style={{padding:"5px 0",textAlign:"right",color:col,fontWeight:700,fontFamily:"var(--mono)"}}>{val}</td>
                            </tr>
                          ))}
                          <tr style={{borderTop:`1px solid ${C.border}`}}>
                            <td colSpan={2} style={{padding:"6px 0",fontWeight:700,color:s.deal_score>=75?C.green:s.deal_score>=50?C.accent:C.muted}}>Toplam</td>
                            <td style={{padding:"6px 0",textAlign:"right",fontWeight:700,fontSize:15,color:s.deal_score>=75?C.green:s.deal_score>=50?C.accent:C.muted,fontFamily:"var(--mono)"}}>{s.deal_score}/100</td>
                          </tr>
                        </tbody>
                      </table>
                    );
                  })()}
                </AccordionSection>
              )}

              {drawerData.cached && (
                <div style={{fontSize:10,color:C.muted3,textAlign:"center",marginTop:12}}>
                  ⚡ Cache · {Math.round((drawerData.cache_age||0)/60)}dk önce
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Footer ───────────────────────────────────────────────────────── */}
        <div style={{padding:"10px 14px",borderTop:`1px solid ${C.border}`,display:"flex",gap:6,flexShrink:0,flexWrap:"wrap"}}>
          {/* eBay — arama */}
          <a href={buildEbaySearchUrl({isbn})} target="_blank" rel="noreferrer"
            style={{flex:1,minWidth:70,padding:"8px 6px",borderRadius:7,background:"#e53238",color:"white",
              textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:11,fontFamily:"var(--mono)"}}>
            🔍 eBay
          </a>
          {/* Amazon — ürün sayfası veya arama */}
          <a href={alertEntry?.asin ? `https://www.amazon.com/dp/${alertEntry.asin}` : `https://www.amazon.com/s?k=${isbn}&i=stripbooks`}
            target="_blank" rel="noreferrer"
            style={{flex:1,minWidth:70,padding:"8px 6px",borderRadius:7,background:"#FF9900",color:"white",
              textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:11,fontFamily:"var(--mono)"}}>
            🛒 Amazon
          </a>
          {/* BookFinder */}
          <a href={`https://www.bookfinder.com/isbn/${isbn}/`} target="_blank" rel="noreferrer"
            style={{flex:1,minWidth:70,padding:"8px 6px",borderRadius:7,background:"#6366f1",color:"white",
              textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:11,fontFamily:"var(--mono)"}}>
            📚 BookFinder
          </a>
          {/* İlan — direkt eBay ilanına git */}
          {alertEntry?.url && (
            <a href={alertEntry.url} target="_blank" rel="noreferrer"
              style={{flex:1,minWidth:70,padding:"8px 6px",borderRadius:7,background:C.surface2,color:C.accent,
                textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:11,fontFamily:"var(--mono)",
                border:`1px solid ${C.accent}`}}>
              🏷 İlan ↗
            </a>
          )}
          <button onClick={onClose}
            style={{padding:"8px 14px",borderRadius:7,background:"none",
              border:`1px solid ${C.border}`,color:C.muted,cursor:"pointer",fontFamily:"var(--mono)",fontSize:11}}>
            ✕
          </button>
        </div>
      </div>
    </>
  );
}


function CandidatesTab({ C, candidates, removeCandidate, saveCandidates, push, isbns, addIsbn }) {
  const [filter, setFilter] = useState("");
  const [sortKey, setSortKey] = useState("addedAt");
  const [sortDir, setSortDir] = useState("desc");
  const [confirmClear, setConfirmClear] = useState(false);

  // Verify
  const [candVerifyResults, setCandVerifyResults] = useState({});
  const [candVerifying, setCandVerifying] = useState(new Set());
  const [candVerifyDrawer, setCandVerifyDrawer] = useState(null);

  const candVerifyOne = async (key, row) => {
    setCandVerifying(prev => new Set([...prev, key]));
    try {
      const res = await fetch("/verify/listing", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({isbn: row.isbn, candidate: row}),
      });
      const data = await res.json();
      setCandVerifyResults(prev => ({...prev, [key]: data}));
    } catch(e) {
      setCandVerifyResults(prev => ({...prev, [key]: {status:"ERROR", summary: e.message}}));
    } finally {
      setCandVerifying(prev => { const s=new Set(prev); s.delete(key); return s; });
    }
  };

  // AI Analysis (single)
  const [aiModal, setAiModal] = useState(null);
  const [analyzingIsbn, setAnalyzingIsbn] = useState(null);

  // Bulk AI Analysis
  const [selected, setSelected] = useState(new Set());
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState({done:0, total:0, current:""});
  const [aiResults, setAiResults] = useState({});  // {isbn: {verdict, confidence, ...}}

  const toggleSelect = (isbn) => setSelected(prev => {
    const next = new Set(prev);
    next.has(isbn) ? next.delete(isbn) : next.add(isbn);
    return next;
  });
  const toggleAll = () => {
    if (selected.size === filtered.length) setSelected(new Set());
    else setSelected(new Set(filtered.map(r => r.isbn)));
  };

  const runAiAnalysis = async (row) => {
    setAnalyzingIsbn(row.isbn);
    setAiModal({isbn: row.isbn, status:"loading", data:null, error:null});
    try {
      const res = await req("/ai/analyze", {method:"POST", body:JSON.stringify({isbn:row.isbn,candidate:row})}, 90000);
      setAiModal({isbn:row.isbn, status:"done", data:res, error:null});
      setAiResults(prev => ({...prev, [row.isbn]: res}));
    } catch(e) {
      setAiModal({isbn:row.isbn, status:"error", data:null, error:e?.message||String(e)});
    } finally {
      setAnalyzingIsbn(null);
    }
  };

  const runBulkAnalysis = async () => {
    const rows = filtered.filter(r => selected.has(r.isbn));
    if (!rows.length) { push("Önce satır seç","info"); return; }
    setBulkRunning(true);
    setBulkProgress({done:0, total:rows.length, current:""});
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      setBulkProgress({done:i, total:rows.length, current:r.isbn});
      try {
        const res = await req("/ai/analyze", {method:"POST", body:JSON.stringify({isbn:r.isbn,candidate:r})}, 90000);
        setAiResults(prev => ({...prev, [r.isbn]: res}));
      } catch(e) {
        setAiResults(prev => ({...prev, [r.isbn]: {verdict:"ERROR", summary:e?.message||String(e), error:true}}));
      }
      // Rate limit: istekler arası 2s bekle
      if (i < rows.length - 1) await new Promise(ok => setTimeout(ok, 2000));
    }
    setBulkProgress({done:rows.length, total:rows.length, current:"Tamamlandı"});
    setBulkRunning(false);
    push(`${rows.length} aday analiz edildi`, "success");
  };
  const verdictStyle = (v) => ({"BUY":{bg:"#22c55e22",color:"#22c55e",label:"✅ AL"},"PASS":{bg:"#ef444422",color:"#ef4444",label:"❌ GEÇME"},"WATCH":{bg:"#f9731622",color:"#f97316",label:"⏳ BEKLE"},"UNKNOWN":{bg:"#6b728022",color:"#6b7280",label:"❓"}})[v]||{bg:"#6b728022",color:"#6b7280",label:v||"?"};
  const trendIcon = (t) => ({"RISING":"📈","STABLE":"➡️","DECLINING":"📉"})[t]||"❓";
  const riskColor = (r) => ({"LOW":C.green,"MEDIUM":"#f97316","HIGH":"#ef4444"})[r]||C.muted;

  const tierColor = (t) => t==="fire"?"#f59e0b":t==="good"?"#22c55e":t==="low"?"#60a5fa":"#6b7280";

  const filtered = candidates
    .filter(r => !filter || r.isbn.includes(filter) || (r.source||"").includes(filter))
    .sort((a,b) => {
      const av = a[sortKey]??0, bv = b[sortKey]??0;
      return sortDir==="desc" ? (bv>av?1:bv<av?-1:0) : (av>bv?1:av<bv?-1:0);
    });

  const inWatchlist = (isbn) => isbns.includes(isbn);

  const SortBtn = ({k,label}) => (
    <button onClick={()=>{if(sortKey===k)setSortDir(d=>d==="desc"?"asc":"desc");else{setSortKey(k);setSortDir("desc");}}}
      style={{background:"none",border:"none",cursor:"pointer",color:sortKey===k?C.accent:C.muted,
        fontSize:10,fontWeight:600,padding:"2px 4px",fontFamily:"var(--mono)"}}>
      {label}{sortKey===k?(sortDir==="desc"?" ↓":" ↑"):""}
    </button>
  );

  return (
    <>
    <div style={{padding:"24px 28px",maxWidth:1400}}>
      {/* AI Analysis Modal */}
      {aiModal&&(
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.65)",zIndex:200,display:"flex",alignItems:"center",justifyContent:"center",padding:16}} onClick={e=>{if(e.target===e.currentTarget)setAiModal(null);}}>
          <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:14,padding:28,width:560,maxWidth:"95vw",maxHeight:"85vh",overflowY:"auto",boxShadow:"0 24px 64px rgba(0,0,0,.5)"}}>
            {/* Modal header */}
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:16}}>
              <div>
                <div style={{fontSize:14,fontWeight:700,color:C.text}}>🤖 AI Analizi</div>
                <div style={{fontSize:11,color:C.muted,fontFamily:"var(--mono)"}}>{aiModal.isbn}</div>
              </div>
              <button onClick={()=>setAiModal(null)} style={{background:"none",border:"none",cursor:"pointer",color:C.muted,fontSize:18,padding:"4px 8px"}}>✕</button>
            </div>

            {aiModal.status==="loading"&&(
              <div style={{textAlign:"center",padding:"40px 0"}}>
                <div style={{fontSize:28,marginBottom:12,animation:"spin 1.5s linear infinite",display:"inline-block"}}>🔍</div>
                <div style={{color:C.muted,fontSize:12}}>Claude web'de araştırıyor...</div>
                <div style={{color:C.muted3,fontSize:10,marginTop:6}}>Fiyat geçmişi, rakipler, trend analizi yapılıyor</div>
              </div>
            )}

            {aiModal.status==="error"&&(
              <div style={{background:"#ef444411",border:"1px solid #ef444433",borderRadius:8,padding:16,color:"#ef4444",fontSize:12}}>
                ❌ Hata: {aiModal.error}
                {aiModal.error?.includes("ANTHROPIC_API_KEY")&&(
                  <div style={{marginTop:8,color:C.muted,fontSize:11}}>
                    Sunucuda: <code style={{background:C.surface2,padding:"2px 6px",borderRadius:3}}>sudo nano /etc/trackerbundle.env</code> → <code>ANTHROPIC_API_KEY=sk-ant-...</code> ekle
                  </div>
                )}
              </div>
            )}

            {aiModal.status==="done"&&aiModal.data&&(()=>{
              const d = aiModal.data;
              const vs = verdictStyle(d.verdict);
              return (
                <div style={{display:"flex",flexDirection:"column",gap:12}}>
                  {/* Verdict */}
                  <div style={{display:"flex",alignItems:"center",gap:10}}>
                    <span style={{padding:"6px 16px",borderRadius:8,fontSize:15,fontWeight:700,background:vs.bg,color:vs.color,border:`1px solid ${vs.color}44`}}>{vs.label}</span>
                    {d.verdict_override&&<span style={{padding:"3px 8px",borderRadius:4,fontSize:9,fontWeight:600,background:"#8b5cf622",color:"#8b5cf6",border:"1px solid #8b5cf644"}} title={d.verdict_override_reason||""}>⚡ Sayısal Override</span>}
                    <div style={{flex:1}}>
                      <div style={{fontSize:11,color:C.muted}}>Güven</div>
                      <div style={{display:"flex",alignItems:"center",gap:6}}>
                        <div style={{flex:1,height:6,background:C.surface2,borderRadius:3,overflow:"hidden"}}>
                          <div style={{width:`${d.confidence||0}%`,height:"100%",background:vs.color,borderRadius:3}}/>
                        </div>
                        <span style={{fontSize:11,fontWeight:700,color:vs.color,fontFamily:"var(--mono)"}}>{d.confidence||0}%</span>
                      </div>
                    </div>
                  </div>

                  {/* Summary */}
                  <div style={{background:C.surface2,borderRadius:8,padding:"10px 14px",fontSize:12,color:C.text,lineHeight:1.6}}>{d.summary}</div>

                  {/* Trend + Risk row */}
                  <div style={{display:"flex",gap:8}}>
                    <div style={{flex:1,background:C.surface2,borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em"}}>Fiyat Trendi</div>
                      <div style={{fontSize:13,fontWeight:700,color:C.text}}>{trendIcon(d.price_trend)} {d.price_trend||"?"}</div>
                      <div style={{fontSize:11,color:C.muted,marginTop:4,lineHeight:1.4}}>{d.price_trend_reason}</div>
                    </div>
                    <div style={{flex:1,background:C.surface2,borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em"}}>Risk Seviyesi</div>
                      <div style={{fontSize:13,fontWeight:700,color:riskColor(d.risk_level)}}>{d.risk_level||"?"}</div>
                      {(d.risks||[]).map((r,i)=>(
                        <div key={i} style={{fontSize:10,color:C.muted,marginTop:3}}>• {r}</div>
                      ))}
                    </div>
                  </div>

                  {/* Competitors */}
                  {d.competitors&&(
                    <div style={{background:C.surface2,borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em"}}>👥 Rakip Satıcılar</div>
                      <div style={{fontSize:11,color:C.text,lineHeight:1.5}}>{d.competitors}</div>
                    </div>
                  )}

                  {/* Buy suggestion */}
                  {d.buy_suggestion&&(
                    <div style={{background:`${C.green}11`,border:`1px solid ${C.green}33`,borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,color:C.green,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em",fontWeight:600}}>💡 Alım Önerisi</div>
                      <div style={{fontSize:11,color:C.text,lineHeight:1.5}}>{d.buy_suggestion}</div>
                    </div>
                  )}

                  {/* ISBN Conflict warning */}
                  {d.isbn_conflict&&(
                    <div style={{background:"#ef444411",border:"2px solid #ef444433",borderRadius:8,padding:"12px 14px"}}>
                      <div style={{fontSize:11,fontWeight:700,color:"#ef4444",marginBottom:6}}>🚨 ISBN ÇAKIŞMASI TESPİT EDİLDİ</div>
                      <div style={{fontSize:11,color:C.text}}>{d.isbn_conflict_note||"Bu ISBN birden fazla farklı kitaba ait olabilir."}</div>
                      <div style={{fontSize:10,color:C.muted,marginTop:6}}>eBay ilanının başlığını ve kapak resmini Amazon'daki kitapla manuel olarak karşılaştır.</div>
                    </div>
                  )}

                  {/* Image verification */}
                  {d.image_verdict&&d.image_verdict!=="NO_IMAGE"&&(
                    <div style={{background:d.image_verdict==="MATCH"?"#22c55e11":d.image_verdict==="MISMATCH"?"#ef444411":"#f9731611",
                      border:`1px solid ${d.image_verdict==="MATCH"?"#22c55e33":d.image_verdict==="MISMATCH"?"#ef444433":"#f9731633"}`,
                      borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,fontWeight:600,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em",
                        color:d.image_verdict==="MATCH"?C.green:d.image_verdict==="MISMATCH"?"#ef4444":"#f97316"}}>
                        📷 Görsel Doğrulama: {d.image_verdict}
                      </div>
                      <div style={{fontSize:11,color:C.text}}>{d.image_notes}</div>
                    </div>
                  )}

                  {/* Edition warning */}
                  {d.has_newer_edition&&(
                    <div style={{background:"#f9731611",border:"1px solid #f9731633",borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,fontWeight:600,color:"#f97316",marginBottom:2,textTransform:"uppercase"}}>⚠️ Yeni Baskı Riski</div>
                      <div style={{fontSize:11,color:C.text}}>
                        {d.edition_year&&`Mevcut baskı: ${d.edition_year} · `}Daha yeni baskı tespit edildi — talep bu baskıya kayabilir.
                      </div>
                    </div>
                  )}

                  {/* Condition flags */}
                  {(d.condition_flags||[]).length>0&&(
                    <div style={{background:"#ef444411",border:"1px solid #ef444433",borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,fontWeight:600,color:"#ef4444",marginBottom:4,textTransform:"uppercase"}}>🚩 Kondisyon Uyarıları</div>
                      {d.condition_flags.map((f,i)=>(
                        <div key={i} style={{fontSize:11,color:C.text,marginTop:2}}>• {f}</div>
                      ))}
                    </div>
                  )}

                  {/* Amazon seller info */}
                  {(d.amazon_is_sold_by_amazon||d.amazon_seller_count!=null)&&(
                    <div style={{background:C.surface2,borderRadius:8,padding:"10px 14px"}}>
                      <div style={{fontSize:9,color:C.muted,marginBottom:4,textTransform:"uppercase",letterSpacing:"0.05em"}}>🏪 Amazon Rakip Durum</div>
                      <div style={{display:"flex",gap:16,flexWrap:"wrap"}}>
                        {d.amazon_seller_count!=null&&<span style={{fontSize:11,color:C.text}}>Toplam satıcı: <b style={{color:C.accent}}>{d.amazon_seller_count}</b></span>}
                        {d.amazon_is_sold_by_amazon&&<span style={{fontSize:11,color:"#ef4444",fontWeight:600}}>🚫 Amazon kendisi satıyor</span>}
                        {d.seasonality_mult!=null&&<span style={{fontSize:11,color:C.muted}}>Mevsimsellik: <b style={{color:d.seasonality_mult>=1.1?C.green:d.seasonality_mult<=0.9?"#ef4444":C.text}}>{d.seasonality_mult}x</b></span>}
                      </div>
                    </div>
                  )}

                  {/* Sources */}
                  {(d.sources_checked||[]).length>0&&(
                    <div style={{fontSize:10,color:C.muted3}}>
                      Kontrol edilen kaynaklar: {(d.sources_checked).join(", ")}
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
        </div>
      )}

      {/* Header */}
      <div style={{display:"flex",alignItems:"center",gap:16,marginBottom:20}}>
        <div>
          <div style={{fontSize:18,fontWeight:700,color:C.text}}>⭐ Watchlist Adayları</div>
          <div style={{fontSize:11,color:C.muted,marginTop:2}}>
            Discover taramasından işaretlediğin kitaplar — Watchlist'e ekleyerek takibe al
          </div>
        </div>
        <div style={{marginLeft:"auto",display:"flex",gap:8,alignItems:"center"}}>
          <input value={filter} onChange={e=>setFilter(e.target.value)} placeholder="ISBN / kaynak ara..."
            className="inp" style={{width:200,fontSize:11,padding:"6px 10px"}}/>
          {candidates.length>0&&(
            confirmClear
              ? <div style={{display:"flex",gap:6,alignItems:"center"}}>
                  <span style={{fontSize:11,color:C.muted}}>Emin misin?</span>
                  <button onClick={()=>{saveCandidates([]);setConfirmClear(false);push("Tüm adaylar silindi","info");}}
                    style={{padding:"5px 10px",fontSize:11,borderRadius:5,cursor:"pointer",background:"#ef444422",color:"#ef4444",border:"1px solid #ef444444"}}>Evet, Temizle</button>
                  <button onClick={()=>setConfirmClear(false)}
                    style={{padding:"5px 10px",fontSize:11,borderRadius:5,cursor:"pointer",background:C.surface2,color:C.muted,border:`1px solid ${C.border}`}}>İptal</button>
                </div>
              : <>
                  <button onClick={runBulkAnalysis}
                    disabled={bulkRunning || selected.size===0}
                    style={{padding:"5px 12px",fontSize:11,borderRadius:5,cursor:bulkRunning?"wait":"pointer",fontWeight:600,
                      background:selected.size>0?"#7c3aed22":"transparent",
                      color:selected.size>0?"#a78bfa":C.muted,
                      border:`1px solid ${selected.size>0?"#7c3aed44":C.border}`,
                      opacity:bulkRunning?0.6:1}}>
                    {bulkRunning?`⏳ ${bulkProgress.done}/${bulkProgress.total}`:`🤖 Toplu AI (${selected.size})`}
                  </button>
                  <button onClick={()=>setConfirmClear(true)}
                    style={{padding:"5px 10px",fontSize:11,borderRadius:5,cursor:"pointer",background:C.surface2,color:C.muted,border:`1px solid ${C.border}`}}>
                    🗑 Tümünü Temizle
                  </button>
                </>
          )}
        </div>
      </div>

      {/* Stats bar */}
      {candidates.length>0&&(
        <div style={{display:"flex",gap:12,marginBottom:16,flexWrap:"wrap"}}>
          {[
            {label:"Toplam Aday",val:candidates.length,color:C.accent},
            {label:"Watchlist'te",val:candidates.filter(r=>inWatchlist(r.isbn)).length,color:"#22c55e"},
            {label:"Bekliyor",val:candidates.filter(r=>!inWatchlist(r.isbn)).length,color:"#f59e0b"},
            {label:"Ort. Kar",val:candidates.filter(r=>r.profit>0).length>0?"$"+Math.round(candidates.filter(r=>r.profit>0).reduce((s,r)=>s+r.profit,0)/candidates.filter(r=>r.profit>0).length):"—",color:C.green},
          ].map(({label,val,color})=>(
            <div key={label} style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 14px",minWidth:110}}>
              <div style={{fontSize:9,color:C.muted,marginBottom:3,textTransform:"uppercase",letterSpacing:"0.05em"}}>{label}</div>
              <div style={{fontSize:16,fontWeight:700,color,fontFamily:"var(--mono)"}}>{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Bulk AI progress */}
      {bulkRunning&&(
        <div style={{background:C.surface,border:`1px solid #7c3aed44`,borderRadius:8,padding:"10px 14px",marginBottom:14}}>
          <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:6}}>
            <span style={{fontSize:11,fontWeight:600,color:"#a78bfa"}}>🤖 Toplu AI Analizi</span>
            <span style={{fontSize:11,color:C.muted}}>{bulkProgress.done}/{bulkProgress.total}</span>
            <span style={{fontSize:10,color:C.muted3,fontFamily:"var(--mono)"}}>{bulkProgress.current}</span>
          </div>
          <div style={{height:4,background:C.surface2,borderRadius:2,overflow:"hidden"}}>
            <div style={{width:`${bulkProgress.total>0?bulkProgress.done/bulkProgress.total*100:0}%`,height:"100%",background:"#7c3aed",borderRadius:2,transition:"width .3s"}}/>
          </div>
        </div>
      )}

      {filtered.length===0?(
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:12,padding:"60px 40px",textAlign:"center"}}>
          <div style={{fontSize:40,marginBottom:12}}>⭐</div>
          <div style={{color:C.text,fontSize:14,fontWeight:600,marginBottom:6}}>Henüz aday yok</div>
          <div style={{color:C.muted,fontSize:12}}>Discover sayfasında tarama yap, sonuçlardaki ☆ butonuna bas</div>
        </div>
      ):(
        <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,overflow:"hidden"}}>
          <div style={{overflowX:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
              <thead>
                <tr style={{background:C.surface2,borderBottom:`1px solid ${C.border}`}}>
                  <th style={{padding:"8px 6px",width:30}}>
                    <input type="checkbox" checked={selected.size===filtered.length && filtered.length>0}
                      onChange={toggleAll} style={{cursor:"pointer"}}/>
                  </th>
                  {[
                    {k:"isbn",l:"ISBN"},{k:"source",l:"Kaynak"},{k:"source_condition",l:"Cond"},
                    {k:"buy_price",l:"Alım $"},{k:"amazon_sell_price",l:"Amazon $"},
                    {k:"profit",l:"Kar $"},{k:"roi_pct",l:"ROI %"},{k:"roi_tier",l:"Tier"},
                    {k:"confidence",l:"Güven"},{k:"ev_score",l:"EV/mo"},
                    {k:"_ai",l:"AI Sonuç"},
                    {k:"buyback_cash",l:"💰 Buyback"},
                    {k:"addedAt",l:"Eklenme"},
                    {k:"_verify",l:"Doğrula"},
                    {k:"_actions",l:"Aksiyon"},
                  ].map(({k,l})=>(
                    <th key={k} style={{padding:"8px 10px",textAlign:"left",color:C.muted,fontWeight:600,whiteSpace:"nowrap"}}>
                      {k.startsWith("_")?l:<SortBtn k={k} label={l}/>}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((r,i)=>{
                  const inWL = inWatchlist(r.isbn);
                  return (
                    <tr key={i} style={{borderBottom:`1px solid ${C.border}`,background:inWL?`${C.green}08`:i%2===0?C.surface:C.surface2,transition:"background .1s"}}>
                      <td style={{padding:"7px 6px",textAlign:"center"}}>
                        <input type="checkbox" checked={selected.has(r.isbn)} onChange={()=>toggleSelect(r.isbn)} style={{cursor:"pointer"}}/>
                      </td>
                      <td style={{padding:"7px 10px",color:C.text,fontFamily:"monospace"}}>
                        <div style={{display:"flex",alignItems:"center",gap:6}}>
                          {r.isbn}
                          {inWL&&<span style={{fontSize:9,padding:"1px 5px",borderRadius:3,background:"#22c55e22",color:"#22c55e",fontWeight:600}}>WL</span>}
                        </div>
                      </td>
                      <td style={{padding:"7px 10px",color:C.accent}}>{r.source||"—"}</td>
                      <td style={{padding:"7px 10px"}}>
                        <span style={{padding:"2px 6px",borderRadius:4,fontSize:10,fontWeight:600,
                          background:r.source_condition==="new"?`${C.green}22`:`${C.accent}22`,
                          color:r.source_condition==="new"?C.green:C.accent}}>
                          {(r.source_condition||"—").toUpperCase()}
                        </span>
                      </td>
                      <td style={{padding:"7px 10px",color:C.text,fontFamily:"var(--mono)"}}>{r.buy_price>0?`$${r.buy_price}`:"—"}</td>
                      <td style={{padding:"7px 10px",color:C.text,fontFamily:"var(--mono)"}}>{r.amazon_sell_price!=null?`$${r.amazon_sell_price}`:"—"}</td>
                      <td style={{padding:"7px 10px",fontWeight:600,fontFamily:"var(--mono)",
                        color:r.profit>0?C.green:"#ef4444"}}>{r.profit!=null?`$${r.profit}`:"—"}</td>
                      <td style={{padding:"7px 10px",fontWeight:600,fontFamily:"var(--mono)",
                        color:tierColor(r.roi_tier)}}>{r.roi_pct!=null?`${r.roi_pct}%`:"—"}</td>
                      <td style={{padding:"7px 10px"}}>
                        {r.roi_tier&&<span style={{padding:"2px 6px",borderRadius:4,fontSize:10,fontWeight:700,
                          background:`${tierColor(r.roi_tier)}22`,color:tierColor(r.roi_tier)}}>
                          {r.roi_tier==="fire"?"🔥":r.roi_tier==="good"?"✅":r.roi_tier==="low"?"🔵":"❌"}
                        </span>}
                      </td>
                      <td style={{padding:"7px 10px",textAlign:"center"}}>
                        {r.confidence!=null&&(
                          <span style={{padding:"2px 6px",borderRadius:4,fontSize:10,fontWeight:700,
                            background:r.confidence>=75?"#22c55e22":r.confidence>=50?"#f9731622":"#ef444422",
                            color:r.confidence>=75?C.green:r.confidence>=50?"#f97316":"#ef4444"}}>
                            {r.confidence}
                          </span>
                        )}
                      </td>
                      <td style={{padding:"7px 10px",textAlign:"center",color:C.green,fontWeight:600,fontFamily:"var(--mono)"}}>
                        {r.ev_score!=null?`$${r.ev_score}`:"—"}
                      </td>
                      <td style={{padding:"7px 10px",textAlign:"center"}}>
                        {(()=>{
                          const ai = aiResults[r.isbn];
                          if (!ai) return <span style={{fontSize:9,color:C.muted3}}>—</span>;
                          if (ai.error) return <span style={{fontSize:9,color:"#ef4444"}}>❌</span>;
                          const vs = verdictStyle(ai.verdict);
                          return (
                            <button onClick={()=>setAiModal({isbn:r.isbn,status:"done",data:ai,error:null})}
                              title={`${ai.verdict}: ${(ai.summary||"").slice(0,60)}`}
                              style={{padding:"2px 8px",borderRadius:4,fontSize:9,fontWeight:700,
                                background:vs.bg,color:vs.color,border:`1px solid ${vs.color}33`,
                                cursor:"pointer"}}>
                              {vs.label}{ai.verdict_override?" ⚡":""}
                              {ai.confidence!=null&&<span style={{marginLeft:3,opacity:0.7}}>{ai.confidence}%</span>}
                            </button>
                          );
                        })()}
                      </td>
                      <td style={{padding:"7px 10px",color:C.muted,fontSize:10}}>
                        {r.addedAt?new Date(r.addedAt).toLocaleDateString("tr-TR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}):"—"}
                      </td>
                      <td style={{padding:"6px 8px", textAlign:"center", minWidth:85}}>
                        {r.buyback_cash!=null ? (
                          <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:1}}>
                            <span style={{fontSize:11,fontWeight:700,
                              color:r.buyback_profit>0?"#22c55e":"#ef4444"}}>
                              ${r.buyback_cash}
                            </span>
                            <span style={{fontSize:9,color:C.muted}}>
                              {r.buyback_profit>0?`+$${r.buyback_profit}`:`−$${Math.abs(r.buyback_profit||0)}`}
                            </span>
                            {r.buyback_vendor&&(
                              <a href={r.buyback_url||"#"} target="_blank" rel="noreferrer"
                                style={{fontSize:8,color:C.accent,textDecoration:"none"}}>
                                {r.buyback_vendor}
                              </a>
                            )}
                          </div>
                        ) : <span style={{color:C.muted3,fontSize:10}}>—</span>}
                      </td>
                      <td style={{padding:"6px 8px",textAlign:"center",minWidth:90}}>
                        {candVerifying.has(r.isbn) ? (
                          <span style={{fontSize:10,color:C.muted}}>⏳</span>
                        ) : candVerifyResults[r.isbn] ? (
                          <div style={{display:"flex",flexDirection:"column",alignItems:"center",gap:2}}>
                            <span style={{fontSize:9,fontWeight:700,cursor:"pointer",
                              color:{VERIFIED:"#22c55e",VERIFIED_STOCK_PHOTO:"#f97316",GONE:"#ef4444",PRICE_UP:"#f97316",PRICE_DOWN:"#3b82f6",MISMATCH:"#ef4444",UNVERIFIABLE:"#64748b"}[candVerifyResults[r.isbn].status]||"#94a3b8"}}
                              onClick={()=>candVerifyOne(r.isbn,r)}>
                              {{VERIFIED:"✅",VERIFIED_STOCK_PHOTO:"📷⚠️",GONE:"💀",PRICE_UP:"📈",PRICE_DOWN:"📉",MISMATCH:"⚠️",UNVERIFIABLE:"ℹ️"}[candVerifyResults[r.isbn].status]||"?"} {candVerifyResults[r.isbn].status}
                            </span>
                            <button onClick={()=>setCandVerifyDrawer({rowIdx:r.isbn,row:r})}
                              style={{padding:"1px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                                background:"#a855f711",color:"#a855f7",border:"1px solid #a855f744",fontFamily:"var(--mono)"}}>
                              🧠 Detay
                            </button>
                          </div>
                        ) : (
                          <button onClick={()=>candVerifyOne(r.isbn,r)}
                            style={{padding:"2px 7px",fontSize:9,borderRadius:4,cursor:"pointer",
                              background:"#0ea5e911",color:"#0ea5e9",border:"1px solid #0ea5e944",fontFamily:"var(--mono)"}}>
                            🔍 Doğrula
                          </button>
                        )}
                      </td>
                      <td style={{padding:"6px 8px",whiteSpace:"nowrap"}}>
                        <div style={{display:"flex",gap:4,alignItems:"center"}}>
                          <button onClick={()=>runAiAnalysis(r)}
                            disabled={analyzingIsbn===r.isbn}
                            title="AI ile analiz et"
                            style={{padding:"4px 8px",fontSize:10,borderRadius:5,cursor:analyzingIsbn===r.isbn?"wait":"pointer",fontWeight:600,
                              background:"#7c3aed22",color:"#a78bfa",border:"1px solid #7c3aed44",
                              opacity:analyzingIsbn===r.isbn?0.6:1}}>
                            {analyzingIsbn===r.isbn?"⏳":"🤖"}
                          </button>
                          {!inWL?(
                            <button onClick={()=>addIsbn(r.isbn)}
                              title="Watchlist'e ekle"
                              style={{padding:"4px 10px",fontSize:10,borderRadius:5,cursor:"pointer",fontWeight:600,
                                background:"#22c55e22",color:"#22c55e",border:"1px solid #22c55e55"}}>
                              + WL
                            </button>
                          ):(
                            <span style={{padding:"4px 8px",fontSize:10,color:C.muted}}>✓ WL'de</span>
                          )}
                          <button onClick={()=>removeCandidate(r.isbn, r.source, r.source_condition)}
                            title="Adaylardan kaldır"
                            style={{padding:"4px 8px",fontSize:10,borderRadius:5,cursor:"pointer",
                              background:"transparent",color:"#ef444488",border:"1px solid #ef444433"}}>
                            ✕
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>

    {candVerifyDrawer && candVerifyResults[candVerifyDrawer.rowIdx] && (
      <VerifyDetailDrawer C={C} data={candVerifyResults[candVerifyDrawer.rowIdx]}
        row={candVerifyDrawer.row} onClose={()=>setCandVerifyDrawer(null)}/>
    )}
  </>
  );
}


// ─── Bookstores Tab ──────────────────────────────────────────────────────────

const AMZN_CONDITIONS = ["acceptable", "good", "very_good", "like_new"];
const AMZN_COND_LABELS = { acceptable: "Acceptable", good: "Good", very_good: "Very Good", like_new: "Like New" };

function BookstoresTab({ C, theme, push }) {
  const [subTab, setSubTab] = useState("bookdepot");
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("date");  // date | price_asc | price_desc | title
  const [maxPrice, setMaxPrice] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [importing, setImporting] = useState(false);

  // Scan state
  const [scanJobId, setScanJobId] = useState(null);
  const [scanProgress, setScanProgress] = useState(null);
  const [scanResults, setScanResults] = useState(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanSort, setScanSort] = useState("roi_desc"); // roi_desc | profit_desc | price_asc

  // Scan filters
  const [scanMinRoi, setScanMinRoi] = useState("");
  const [scanMinProfit, setScanMinProfit] = useState("");
  const [scanCompareWith, setScanCompareWith] = useState("used");
  const [scanAmazonConds, setScanAmazonConds] = useState([...AMZN_CONDITIONS]);
  const [scanConcurrency, setScanConcurrency] = useState(5);
  const [showScanFilters, setShowScanFilters] = useState(false);

  // Manuel ISBN input
  const [useManualIsbns, setUseManualIsbns] = useState(false);
  const [manualIsbnText, setManualIsbnText] = useState("");

  // History
  const [bdHistory, setBdHistory] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [expandedHistory, setExpandedHistory] = useState(null);

  const fetchInventory = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (minPrice) params.set("min_price", minPrice);
      if (maxPrice) params.set("max_price", maxPrice);
      const qs = params.toString();
      const data = await req(`/bookdepot/inventory${qs ? "?" + qs : ""}`);
      setItems(data.items || []);
    } catch (e) {
      push("Envanter yüklenemedi: " + e.message, "error");
    } finally {
      setLoading(false);
    }
  }, [minPrice, maxPrice, push]);

  useEffect(() => { fetchInventory(); }, [fetchInventory]);

  const clearInventory = async () => {
    if (!confirm("Tüm BookDepot envanterini silmek istediğine emin misin?")) return;
    try {
      await req("/bookdepot/inventory", { method: "DELETE" });
      setItems([]);
      push("Envanter temizlendi", "success");
    } catch (e) {
      push("Silinemedi: " + e.message, "error");
    }
  };

  const importToWatchlist = async (isbn) => {
    setImporting(true);
    try {
      const res = await req("/isbns", { method: "POST", body: JSON.stringify({ isbn }) });
      if (res.added) {
        push(isbn + " watchlist'e eklendi", "success");
      } else {
        push("Zaten watchlist'te", "info");
      }
    } catch (e) {
      push("Eklenemedi: " + e.message, "error");
    } finally {
      setImporting(false);
    }
  };

  const importAllToWatchlist = async () => {
    if (!confirm(`${filtered.length} ISBN'i watchlist'e eklemek istediğine emin misin?`)) return;
    setImporting(true);
    let added = 0;
    for (const item of filtered) {
      try {
        const res = await req("/isbns", { method: "POST", body: JSON.stringify({ isbn: item.isbn }) });
        if (res.added) added++;
      } catch {}
    }
    push(`${added} ISBN watchlist'e eklendi`, "success");
    setImporting(false);
  };

  // History fetch
  const fetchHistory = async () => {
    setHistoryLoading(true);
    try {
      const data = await req("/bookdepot/history");
      setBdHistory(data.history || []);
    } catch (e) {
      push("Geçmiş yüklenemedi: " + e.message, "error");
    } finally {
      setHistoryLoading(false);
    }
  };

  // Dosyadan ISBN yükle
  const handleIsbnFile = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const text = await file.text();
    // ISBN'leri satır veya virgülle ayır, 10-13 haneli sayısal değerleri al
    const isbns = text.split(/[\n\r,;\t]+/)
      .map(s => s.replace(/[^0-9X]/gi, "").trim())
      .filter(s => s.length >= 10 && s.length <= 13);
    setManualIsbnText(prev => {
      const existing = prev.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
      const combined = [...new Set([...existing, ...isbns])];
      return combined.join("\n");
    });
    push(`${isbns.length} ISBN dosyadan yüklendi`, "success");
    e.target.value = "";
  };

  // Scan functions
  const startScan = async () => {
    setScanLoading(true);
    setScanResults(null);

    let manualIsbns = null;
    if (useManualIsbns) {
      manualIsbns = manualIsbnText
        .split(/[\n\r,;\t\s]+/)
        .map(s => s.replace(/[^0-9X]/gi, "").trim())
        .filter(s => s.length >= 10);
      if (manualIsbns.length === 0) {
        push("Manuel ISBN listesi boş", "error");
        setScanLoading(false);
        return;
      }
    }

    const body = {
      only_viable: true,
      concurrency: scanConcurrency,
      compare_with: scanCompareWith,
      amazon_condition_in: scanAmazonConds.length < AMZN_CONDITIONS.length ? scanAmazonConds : null,
      min_roi_pct: scanMinRoi ? parseFloat(scanMinRoi) : null,
      min_profit_usd: scanMinProfit ? parseFloat(scanMinProfit) : null,
      ...(manualIsbns ? { isbns: manualIsbns } : {}),
    };

    try {
      const res = await req("/bookdepot/scan", { method: "POST", body: JSON.stringify(body) });
      if (res.ok) {
        setScanJobId(res.job_id);
        push(`Tarama başladı — ${res.total} ISBN`, "success");
        setSubTab("scan");
      } else {
        push(res.message || "Tarama başlatılamadı", "error");
        setScanLoading(false);
      }
    } catch (e) {
      push("Tarama hatası: " + e.message, "error");
      setScanLoading(false);
    }
  };

  const cancelScan = async () => {
    if (!scanJobId) return;
    try {
      await req(`/discover/csv-arb/cancel/${scanJobId}`, { method: "POST" });
    } catch {}
  };

  const deleteUnprofitable = async () => {
    const lastJobId = scanResults?._job_id || scanProgress?.id;
    if (!lastJobId) return;
    const rejCount = scanResults?.rejected?.length || 0;
    if (!confirm(`${rejCount} karlı olmayan ISBN envanterden silinsin mi?`)) return;
    try {
      const res = await req(`/bookdepot/inventory/unprofitable?job_id=${lastJobId}`, { method: "DELETE" });
      push(`${res.deleted} ISBN envanterden silindi`, "success");
      fetchInventory();
    } catch (e) {
      push("Silinemedi: " + e.message, "error");
    }
  };

  // Poll scan progress
  useEffect(() => {
    if (!scanJobId) return;
    let alive = true;
    const poll = async () => {
      try {
        const p = await req(`/discover/csv-arb/progress/${scanJobId}`);
        if (!alive) return;
        setScanProgress(p);
        if (p.status === "done" || p.status === "error" || p.status === "cancelled") {
          setScanLoading(false);
          setScanResults({ accepted: p.accepted || [], rejected: p.rejected || [], _job_id: p.id });
          setScanJobId(null);
          fetchHistory();
        }
      } catch {}
    };
    poll();
    const iv = setInterval(poll, 2000);
    return () => { alive = false; clearInterval(iv); };
  }, [scanJobId]);

  // Tarama sırasında partial, bittikten sonra final sonuçları göster
  const liveAccepted = scanLoading
    ? (scanProgress?.accepted || [])
    : (scanResults?.accepted || []);

  const scanFiltered = liveAccepted
    .sort((a, b) => {
      if (scanSort === "profit_desc") return (b.profit || 0) - (a.profit || 0);
      if (scanSort === "price_asc") return (a.buy_price || 0) - (b.buy_price || 0);
      return (b.roi_pct || 0) - (a.roi_pct || 0);
    });

  // Filter & sort
  const filtered = items
    .filter(i => {
      if (!search) return true;
      const q = search.toLowerCase();
      return (i.isbn || "").toLowerCase().includes(q) || (i.title || "").toLowerCase().includes(q);
    })
    .sort((a, b) => {
      if (sortBy === "price_asc") return (a.price || 0) - (b.price || 0);
      if (sortBy === "price_desc") return (b.price || 0) - (a.price || 0);
      if (sortBy === "title") return (a.title || "").localeCompare(b.title || "");
      return (b.scraped_at || 0) - (a.scraped_at || 0);
    });

  const totalValue = items.reduce((s, i) => s + (i.price || 0), 0);

  const SUB_TABS = [
    { id: "bookdepot", label: "📦 BookDepot", count: items.length },
    { id: "scan", label: "🔍 Amazon Tarama", count: scanResults?.accepted?.length || (scanLoading ? scanProgress?.accepted_count : 0) || 0 },
    { id: "history", label: "📜 Geçmiş", count: bdHistory.length },
  ];

  const handleSubTab = (id) => {
    setSubTab(id);
    if (id === "history" && bdHistory.length === 0) fetchHistory();
  };

  return (
    <div>
      {/* Sub-tab bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16 }}>
        {SUB_TABS.map(st => (
          <button key={st.id} onClick={() => handleSubTab(st.id)} style={{
            padding: "7px 16px", borderRadius: 6, fontSize: 12, cursor: "pointer",
            fontFamily: "var(--mono)", fontWeight: subTab === st.id ? 600 : 400,
            background: subTab === st.id ? C.accent : "transparent",
            color: subTab === st.id ? C.accentText : C.muted,
            border: `1px solid ${subTab === st.id ? C.accent : C.border}`,
            transition: "all .15s",
          }}>
            {st.label} {st.count > 0 && <span style={{
              marginLeft: 6, padding: "1px 6px", borderRadius: 8, fontSize: 10,
              background: subTab === st.id ? "rgba(255,255,255,.2)" : C.surface2,
              color: subTab === st.id ? C.accentText : C.muted,
            }}>{st.count}</span>}
          </button>
        ))}
      </div>

      {subTab === "bookdepot" && (
        <div>
          {/* Stats bar */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 16 }}>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px" }}>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Toplam Kitap</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.text }}>{items.length}</div>
            </div>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px" }}>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Toplam Değer</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.green }}>${totalValue.toFixed(2)}</div>
            </div>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px" }}>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Ort. Fiyat</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.accent }}>${items.length ? (totalValue / items.length).toFixed(2) : "0.00"}</div>
            </div>
            <div style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 10, padding: "12px 14px" }}>
              <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Gösterilen</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: C.blue }}>{filtered.length}</div>
            </div>
          </div>

          {/* Toolbar */}
          <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
            <input className="inp" placeholder="ISBN veya başlık ara…" value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ flex: 1, minWidth: 180 }} />
            <input className="inp" type="number" placeholder="Min $" value={minPrice}
              onChange={e => setMinPrice(e.target.value)}
              onBlur={fetchInventory}
              style={{ width: 80 }} />
            <input className="inp" type="number" placeholder="Max $" value={maxPrice}
              onChange={e => setMaxPrice(e.target.value)}
              onBlur={fetchInventory}
              style={{ width: 80 }} />
            <select className="inp" value={sortBy} onChange={e => setSortBy(e.target.value)}
              style={{ width: 130 }}>
              <option value="date">Yeni eklenen</option>
              <option value="price_asc">Fiyat ↑</option>
              <option value="price_desc">Fiyat ↓</option>
              <option value="title">Başlık A-Z</option>
            </select>
            <button className="icon-btn" onClick={fetchInventory} title="Yenile"
              style={{ fontSize: 16 }}>↻</button>
            {filtered.length > 0 && (
              <button className="add-btn" onClick={importAllToWatchlist} disabled={importing}
                style={{ fontSize: 11, padding: "6px 14px" }}>
                {importing ? "⏳" : `📋 Tümünü Watchlist'e (${filtered.length})`}
              </button>
            )}
            {items.length > 0 && (
              <button onClick={clearInventory}
                style={{ background: "none", border: `1px solid ${C.red}44`, borderRadius: 6,
                  color: C.red, fontSize: 11, padding: "6px 12px", cursor: "pointer",
                  fontFamily: "var(--mono)" }}>
                🗑
              </button>
            )}
          </div>

          {/* Items list */}
          {loading ? (
            <div style={{ textAlign: "center", padding: 40, color: C.muted3 }}>Yükleniyor…</div>
          ) : filtered.length === 0 ? (
            <div style={{ textAlign: "center", padding: 60, color: C.muted3 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📦</div>
              <div style={{ fontSize: 13, marginBottom: 6 }}>
                {items.length === 0 ? "Henüz kitap yok" : "Filtre sonucu boş"}
              </div>
              <div style={{ fontSize: 11, color: C.muted }}>
                BookDepot bookmarklet ile kitap ekle
              </div>
            </div>
          ) : (
            <div>
              {filtered.map((item, idx) => (
                <div key={item.isbn + idx} className="row-item"
                  style={{ background: C.rowBg, border: `1px solid ${C.rowBorder}`, display: "flex", alignItems: "center", gap: 10 }}>
                  {/* Cover */}
                  <div style={{ flexShrink: 0, width: 32, height: 44, borderRadius: 3, overflow: "hidden", background: C.surface2 }}>
                    <img src={`https://covers.openlibrary.org/b/isbn/${item.isbn}-S.jpg`}
                      loading="lazy" alt=""
                      style={{ width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                      onError={e => { e.target.style.opacity = "0"; }} />
                  </div>
                  {/* Info */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span style={{ fontSize: 12, fontFamily: "var(--mono)", color: C.text }}>{item.isbn}</span>
                      {item.qty && <span className="badge" style={{ background: C.surface2, color: C.muted, fontSize: 9 }}>Stok: {item.qty}</span>}
                    </div>
                    {item.title && (
                      <div style={{ fontSize: 11, color: C.muted, marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.title}
                      </div>
                    )}
                  </div>
                  {/* Price */}
                  <div style={{ flexShrink: 0, textAlign: "right" }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: C.green, fontFamily: "var(--mono)" }}>
                      ${(item.price || 0).toFixed(2)}
                    </div>
                  </div>
                  {/* Actions */}
                  <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                    {item.url && (
                      <a href={item.url} target="_blank" rel="noopener"
                        style={{ padding: "4px 8px", borderRadius: 4, border: `1px solid ${C.border}`,
                          color: C.muted, fontSize: 11, textDecoration: "none",
                          fontFamily: "var(--mono)" }}
                        title="BookDepot'ta aç">
                        🔗
                      </a>
                    )}
                    <button onClick={() => importToWatchlist(item.isbn)} disabled={importing}
                      style={{ padding: "4px 8px", borderRadius: 4, border: `1px solid ${C.accent}44`,
                        background: "transparent", color: C.accent, fontSize: 11,
                        cursor: "pointer", fontFamily: "var(--mono)" }}
                      title="Watchlist'e ekle">
                      + WL
                    </button>
                    <button onClick={() => { navigator.clipboard.writeText(item.isbn); push("Kopyalandı", "info"); }}
                      style={{ padding: "4px 8px", borderRadius: 4, border: `1px solid ${C.border}`,
                        background: "transparent", color: C.muted, fontSize: 11,
                        cursor: "pointer", fontFamily: "var(--mono)" }}
                      title="ISBN kopyala">
                      📋
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {subTab === "scan" && (
        <div>
          {/* ── Kaynak Seçimi ── */}
          <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
            <button onClick={() => setUseManualIsbns(false)}
              style={{ padding: "7px 16px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                fontFamily: "var(--mono)", fontWeight: !useManualIsbns ? 600 : 400,
                background: !useManualIsbns ? C.accent : "transparent",
                color: !useManualIsbns ? C.accentText : C.muted,
                border: `1px solid ${!useManualIsbns ? C.accent : C.border}` }}>
              📦 Envanteri Tara {items.length > 0 && `(${items.length})`}
            </button>
            <button onClick={() => setUseManualIsbns(true)}
              style={{ padding: "7px 16px", borderRadius: 6, fontSize: 12, cursor: "pointer",
                fontFamily: "var(--mono)", fontWeight: useManualIsbns ? 600 : 400,
                background: useManualIsbns ? C.accent : "transparent",
                color: useManualIsbns ? C.accentText : C.muted,
                border: `1px solid ${useManualIsbns ? C.accent : C.border}` }}>
              ✏️ Manuel ISBN Listesi
            </button>
          </div>

          {/* ── Manuel ISBN Input ── */}
          {useManualIsbns && (
            <div style={{ marginBottom: 14, background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8, padding: 12 }}>
              <div style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
                <span style={{ fontSize: 11, color: C.muted, fontFamily: "var(--mono)" }}>
                  ISBN listesi (her satıra bir veya virgülle ayır):
                </span>
                <label style={{ padding: "4px 10px", borderRadius: 4, border: `1px solid ${C.border}`,
                  fontSize: 11, color: C.muted, cursor: "pointer", fontFamily: "var(--mono)" }}>
                  📂 Dosya Yükle
                  <input type="file" accept=".csv,.txt" onChange={handleIsbnFile} style={{ display: "none" }} />
                </label>
                {manualIsbnText && (
                  <button onClick={() => setManualIsbnText("")}
                    style={{ padding: "4px 8px", borderRadius: 4, border: `1px solid ${C.red}44`,
                      background: "none", color: C.red, fontSize: 10, cursor: "pointer" }}>
                    Temizle
                  </button>
                )}
              </div>
              <textarea
                value={manualIsbnText}
                onChange={e => setManualIsbnText(e.target.value)}
                placeholder={"9780134042435\n9780061965784\n..."}
                rows={5}
                style={{ width: "100%", boxSizing: "border-box", background: C.surface2,
                  border: `1px solid ${C.border}`, borderRadius: 6, color: C.text,
                  fontFamily: "var(--mono)", fontSize: 12, padding: "8px 10px", resize: "vertical" }}
              />
              {manualIsbnText && (
                <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
                  {manualIsbnText.split(/[\n\r,;\t\s]+/).filter(s => s.replace(/[^0-9X]/gi,"").length >= 10).length} ISBN tespit edildi
                </div>
              )}
            </div>
          )}

          {/* ── Filtreler ── */}
          <div style={{ marginBottom: 14 }}>
            <button onClick={() => setShowScanFilters(v => !v)}
              style={{ background: "none", border: `1px solid ${C.border}`, borderRadius: 6,
                color: C.muted, fontSize: 11, padding: "6px 14px", cursor: "pointer",
                fontFamily: "var(--mono)" }}>
              {showScanFilters ? "▲" : "▼"} Filtreler
              {(scanMinRoi || scanMinProfit || scanCompareWith !== "used" || scanAmazonConds.length < AMZN_CONDITIONS.length) && (
                <span style={{ marginLeft: 6, padding: "1px 5px", borderRadius: 8,
                  background: C.accent, color: C.accentText, fontSize: 9 }}>●</span>
              )}
            </button>

            {showScanFilters && (
              <div style={{ marginTop: 10, background: C.surface, border: `1px solid ${C.border}`,
                borderRadius: 8, padding: "14px 16px", display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>

                {/* Min ROI */}
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Min ROI %</div>
                  <input className="inp" type="number" placeholder="örn. 20"
                    value={scanMinRoi} onChange={e => setScanMinRoi(e.target.value)}
                    style={{ width: "100%" }} />
                </div>

                {/* Min Kar */}
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>Min Kar $</div>
                  <input className="inp" type="number" placeholder="örn. 3"
                    value={scanMinProfit} onChange={e => setScanMinProfit(e.target.value)}
                    style={{ width: "100%" }} />
                </div>

                {/* Amazon Fiyat Karşılaştırma */}
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 6 }}>Amazon Fiyat Karşılaştırma</div>
                  <div style={{ display: "flex", gap: 6 }}>
                    {["used", "new"].map(v => (
                      <button key={v} onClick={() => setScanCompareWith(v)}
                        style={{ flex: 1, padding: "6px 0", borderRadius: 5, fontSize: 11,
                          cursor: "pointer", fontFamily: "var(--mono)",
                          background: scanCompareWith === v ? C.accent : "transparent",
                          color: scanCompareWith === v ? C.accentText : C.muted,
                          border: `1px solid ${scanCompareWith === v ? C.accent : C.border}`,
                          fontWeight: scanCompareWith === v ? 600 : 400 }}>
                        {v === "used" ? "Used" : "New"}
                      </button>
                    ))}
                  </div>
                  <div style={{ fontSize: 9, color: C.muted3, marginTop: 4 }}>
                    {scanCompareWith === "new" ? "Amazon NEW fiyatıyla kar hesapla" : "Amazon USED fiyatıyla kar hesapla"}
                  </div>
                </div>

                {/* Amazon Condition */}
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 6 }}>Amazon Used Buybox Condition</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {AMZN_CONDITIONS.map(cond => (
                      <label key={cond} style={{ display: "flex", alignItems: "center", gap: 6,
                        fontSize: 11, color: C.text, cursor: "pointer" }}>
                        <input type="checkbox"
                          checked={scanAmazonConds.includes(cond)}
                          onChange={e => setScanAmazonConds(prev =>
                            e.target.checked ? [...prev, cond] : prev.filter(c => c !== cond)
                          )} />
                        {AMZN_COND_LABELS[cond]}
                      </label>
                    ))}
                  </div>
                  <div style={{ fontSize: 9, color: C.muted3, marginTop: 4 }}>
                    Amazon'daki used buybox condition filtresi
                  </div>
                </div>

                {/* Concurrency */}
                <div>
                  <div style={{ fontSize: 10, color: C.muted, marginBottom: 4 }}>
                    Tarama Hızı: {scanConcurrency} eşzamanlı
                  </div>
                  <input type="range" min={1} max={8} value={scanConcurrency}
                    onChange={e => setScanConcurrency(Number(e.target.value))}
                    style={{ width: "100%" }} />
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 9, color: C.muted3 }}>
                    <span>Yavaş</span><span>Hızlı</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* ── Tarama Kontrolleri ── */}
          <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center", flexWrap: "wrap" }}>
            <button className="add-btn" onClick={startScan}
              disabled={scanLoading || (!useManualIsbns && items.length === 0)}
              style={{ fontSize: 12, padding: "8px 20px" }}>
              {scanLoading ? "⏳ Taranıyor…" : "🔍 Taramayı Başlat"}
            </button>
            {scanLoading && (
              <button onClick={cancelScan}
                style={{ padding: "8px 16px", borderRadius: 6, border: `1px solid ${C.red}44`,
                  background: "none", color: C.red, fontSize: 12, cursor: "pointer",
                  fontFamily: "var(--mono)" }}>
                ⏹ Durdur
              </button>
            )}
            {(scanResults || scanLoading) && (
              <select className="inp" value={scanSort} onChange={e => setScanSort(e.target.value)}
                style={{ width: 140 }}>
                <option value="roi_desc">ROI ↓</option>
                <option value="profit_desc">Kar ↓</option>
                <option value="price_asc">Alış ↑</option>
              </select>
            )}
            {scanResults && !scanLoading && (
              <span style={{ fontSize: 11, color: C.muted }}>
                ✅ {scanResults.accepted.length} karlı · ❌ {scanResults.rejected.length} reddedildi
              </span>
            )}
          </div>

          {/* ── Progress Bar ── */}
          {scanLoading && scanProgress && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                <span style={{ fontSize: 11, color: C.muted }}>
                  {scanProgress.progress || 0} / {scanProgress.total || 0} ISBN
                </span>
                <span style={{ fontSize: 11, color: C.muted }}>
                  {scanProgress.eta_s ? `~${Math.ceil(scanProgress.eta_s)}s kaldı` : ""}
                </span>
              </div>
              <div style={{ height: 6, background: C.surface2, borderRadius: 3, overflow: "hidden" }}>
                <div style={{
                  width: `${scanProgress.total ? (scanProgress.progress / scanProgress.total * 100) : 0}%`,
                  height: "100%", background: C.accent, borderRadius: 3, transition: "width .3s",
                }} />
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: 4 }}>
                {scanProgress.accepted_count > 0 && (
                  <span style={{ fontSize: 10, color: C.green }}>
                    ✅ {scanProgress.accepted_count} karlı bulundu
                  </span>
                )}
                {scanProgress.rejected_count > 0 && (
                  <span style={{ fontSize: 10, color: C.muted }}>
                    ❌ {scanProgress.rejected_count} reddedildi
                  </span>
                )}
              </div>
            </div>
          )}

          {/* ── Karlı Olmayan Sil Butonu ── */}
          {scanResults && !scanLoading && scanResults.rejected?.length > 0 && !useManualIsbns && (
            <div style={{ marginBottom: 14 }}>
              <button onClick={deleteUnprofitable}
                style={{ padding: "7px 16px", borderRadius: 6, border: `1px solid ${C.red}44`,
                  background: "none", color: C.red, fontSize: 11, cursor: "pointer",
                  fontFamily: "var(--mono)" }}>
                🗑 Karlı Olmayan {scanResults.rejected.length} ISBN'i Envanterden Sil
              </button>
            </div>
          )}

          {/* ── Boş Durum ── */}
          {!useManualIsbns && items.length === 0 && !scanResults && !scanLoading && (
            <div style={{ textAlign: "center", padding: 60, color: C.muted3 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📦</div>
              <div style={{ fontSize: 13 }}>Önce BookDepot sekmesinden kitap ekleyin</div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 6 }}>
                veya yukarıdan "Manuel ISBN Listesi" seçin
              </div>
            </div>
          )}

          {/* ── Sonuç Tablosu (canlı + final) ── */}
          {scanFiltered.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "var(--mono)" }}>
                <thead>
                  <tr style={{ borderBottom: `2px solid ${C.border}`, textAlign: "left" }}>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500 }}>ISBN</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500 }}>Kaynak</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Alış $</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Amazon $</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Kar $</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500, textAlign: "right" }}>ROI %</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500 }}>Tier</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500, textAlign: "right" }}>BSR</th>
                    <th style={{ padding: "8px 6px", color: C.muted, fontWeight: 500 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {scanFiltered.map((r, idx) => {
                    const tierColor = r.roi_tier === "fire" ? C.orange : r.roi_tier === "good" ? C.green : r.roi_tier === "low" ? C.yellow : C.red;
                    const tierIcon = r.roi_tier === "fire" ? "🔥" : r.roi_tier === "good" ? "✅" : r.roi_tier === "low" ? "⚠️" : "❌";
                    return (
                      <tr key={r.isbn + r.source + idx}
                        style={{ borderBottom: `1px solid ${C.border}22`, background: idx % 2 === 0 ? "transparent" : C.surface }}>
                        <td style={{ padding: "7px 6px", color: C.text }}>{r.isbn}</td>
                        <td style={{ padding: "7px 6px", color: C.muted }}>{r.source}</td>
                        <td style={{ padding: "7px 6px", color: C.text, textAlign: "right" }}>${(r.buy_price || 0).toFixed(2)}</td>
                        <td style={{ padding: "7px 6px", color: C.blue, textAlign: "right" }}>${(r.sell_price || r.amazon_sell_price || 0).toFixed(2)}</td>
                        <td style={{ padding: "7px 6px", color: (r.profit || 0) > 0 ? C.green : C.red, textAlign: "right", fontWeight: 600 }}>
                          ${(r.profit || 0).toFixed(2)}
                        </td>
                        <td style={{ padding: "7px 6px", color: tierColor, textAlign: "right", fontWeight: 600 }}>
                          {(r.roi_pct || 0).toFixed(1)}%
                        </td>
                        <td style={{ padding: "7px 6px" }}>{tierIcon}</td>
                        <td style={{ padding: "7px 6px", color: C.muted, textAlign: "right" }}>
                          {r.bsr ? r.bsr.toLocaleString() : "-"}
                        </td>
                        <td style={{ padding: "7px 6px" }}>
                          <button onClick={() => importToWatchlist(r.isbn)}
                            style={{ padding: "2px 6px", borderRadius: 3, border: `1px solid ${C.accent}44`,
                              background: "transparent", color: C.accent, fontSize: 10,
                              cursor: "pointer", fontFamily: "var(--mono)" }}>
                            +WL
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Tarama bitti ama sonuç yok */}
          {scanResults && scanFiltered.length === 0 && !scanLoading && (
            <div style={{ textAlign: "center", padding: 40, color: C.muted3 }}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>😔</div>
              <div style={{ fontSize: 12 }}>Karlı arbitrage bulunamadı</div>
              <div style={{ fontSize: 10, color: C.muted, marginTop: 4 }}>
                {scanResults.rejected.length} ISBN reddedildi — filtreleri gevşetmeyi dene
              </div>
            </div>
          )}
        </div>
      )}

      {subTab === "history" && (
        <div>
          <div style={{ display: "flex", gap: 10, marginBottom: 14, alignItems: "center" }}>
            <span style={{ fontSize: 13, color: C.text, fontWeight: 600 }}>Geçmiş Taramalar</span>
            <button className="icon-btn" onClick={fetchHistory} title="Yenile" style={{ fontSize: 16 }}>↻</button>
          </div>

          {historyLoading ? (
            <div style={{ textAlign: "center", padding: 40, color: C.muted3 }}>Yükleniyor…</div>
          ) : bdHistory.length === 0 ? (
            <div style={{ textAlign: "center", padding: 60, color: C.muted3 }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>📜</div>
              <div style={{ fontSize: 13 }}>Henüz tarama geçmişi yok</div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 4 }}>İlk BookDepot taramasını yaptıktan sonra burada görünür</div>
            </div>
          ) : (
            <div>
              {bdHistory.map((entry, idx) => {
                const date = new Date(entry.ts * 1000);
                const dateStr = date.toLocaleDateString("tr-TR") + " " + date.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" });
                const acceptedCount = entry.accepted?.length || 0;
                const rejectedCount = entry.rejected_count || 0;
                const total = acceptedCount + rejectedCount;
                const isExpanded = expandedHistory === idx;
                return (
                  <div key={entry.job_id || idx}
                    style={{ background: C.surface, border: `1px solid ${C.border}`, borderRadius: 8,
                      marginBottom: 8, overflow: "hidden" }}>
                    <div onClick={() => setExpandedHistory(isExpanded ? null : idx)}
                      style={{ padding: "12px 14px", display: "flex", alignItems: "center", gap: 12,
                        cursor: "pointer", userSelect: "none" }}>
                      <span style={{ fontSize: 11, color: C.muted, fontFamily: "var(--mono)", minWidth: 120 }}>
                        {dateStr}
                      </span>
                      <span style={{ fontSize: 11, color: C.muted }}>
                        {total} ISBN tarandı
                      </span>
                      <span style={{ fontSize: 11, color: C.green, fontWeight: 600 }}>
                        ✅ {acceptedCount} karlı
                      </span>
                      <span style={{ fontSize: 11, color: C.muted }}>
                        ❌ {rejectedCount} reddedildi
                      </span>
                      {acceptedCount > 0 && (
                        <span style={{ marginLeft: "auto", fontSize: 11, color: C.accent }}>
                          En yüksek ROI: {Math.max(...(entry.accepted || []).map(r => r.roi_pct || 0)).toFixed(0)}%
                        </span>
                      )}
                      <span style={{ fontSize: 12, color: C.muted3 }}>{isExpanded ? "▲" : "▼"}</span>
                    </div>

                    {isExpanded && entry.accepted?.length > 0 && (
                      <div style={{ borderTop: `1px solid ${C.border}`, overflowX: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, fontFamily: "var(--mono)" }}>
                          <thead>
                            <tr style={{ background: C.surface2 }}>
                              <th style={{ padding: "7px 10px", color: C.muted, fontWeight: 500, textAlign: "left" }}>ISBN</th>
                              <th style={{ padding: "7px 10px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Alış $</th>
                              <th style={{ padding: "7px 10px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Amazon $</th>
                              <th style={{ padding: "7px 10px", color: C.muted, fontWeight: 500, textAlign: "right" }}>Kar $</th>
                              <th style={{ padding: "7px 10px", color: C.muted, fontWeight: 500, textAlign: "right" }}>ROI %</th>
                            </tr>
                          </thead>
                          <tbody>
                            {[...entry.accepted].sort((a,b) => (b.roi_pct||0)-(a.roi_pct||0)).map((r, i) => {
                              const tierColor = r.roi_tier === "fire" ? C.orange : r.roi_tier === "good" ? C.green : r.roi_tier === "low" ? C.yellow : C.red;
                              return (
                                <tr key={r.isbn + i} style={{ borderTop: `1px solid ${C.border}22` }}>
                                  <td style={{ padding: "6px 10px", color: C.text }}>{r.isbn}</td>
                                  <td style={{ padding: "6px 10px", textAlign: "right" }}>${(r.buy_price||0).toFixed(2)}</td>
                                  <td style={{ padding: "6px 10px", color: C.blue, textAlign: "right" }}>${(r.sell_price||r.amazon_sell_price||0).toFixed(2)}</td>
                                  <td style={{ padding: "6px 10px", color: (r.profit||0)>0?C.green:C.red, textAlign: "right", fontWeight: 600 }}>${(r.profit||0).toFixed(2)}</td>
                                  <td style={{ padding: "6px 10px", color: tierColor, textAlign: "right", fontWeight: 600 }}>{(r.roi_pct||0).toFixed(1)}%</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {isExpanded && (!entry.accepted || entry.accepted.length === 0) && (
                      <div style={{ padding: "12px 14px", fontSize: 11, color: C.muted, borderTop: `1px solid ${C.border}` }}>
                        Bu taramada karlı sonuç bulunamadı
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ─── Settings Tab ─────────────────────────────────────────────────────────────

const TABS = ["dashboard","watchlist","discover","alerts","pricing","bookstores"];

export default function App() {
  return <ErrorBoundary><AppReal /></ErrorBoundary>;
}
function AppReal() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("tb_theme") || "dark"; } catch { return "dark"; }
  });
  const isDark = theme === "dark";
  const C = theme==="dark" ? DARK : theme==="soft" ? SOFT : LIGHT;
  const [blueFilter, setBlueFilter] = useState(() => { try { return localStorage.getItem("tb_blue")=="1"; } catch { return false; } });
  useEffect(() => {
    try { localStorage.setItem("tb_theme", theme); } catch {}
  }, [theme]);
  useEffect(() => {
    try { localStorage.setItem("tb_blue", blueFilter?"1":"0"); } catch {}
    document.documentElement.style.filter = blueFilter ? "sepia(0.15) saturate(0.85) hue-rotate(-5deg)" : "none";
  }, [blueFilter]);
  const [tab, setTab] = useState("dashboard");
  // ── Global scan state — tab değişince kaybolmaz ──────────────────
  const [scanJob, setScanJob] = useState(null);       // {jobId, progress, results, error, scanning}
  const scanPollRef = useRef(null);

  // ── Watchlist Adayları ───────────────────────────────────────────────────
  const [candidates, setCandidates] = useState(() => {
    try { return JSON.parse(localStorage.getItem("tb_candidates")||"[]"); } catch { return []; }
  });
  const saveCandidates = (list) => {
    setCandidates(list);
    try { localStorage.setItem("tb_candidates", JSON.stringify(list)); } catch {}
  };
  const addCandidate = (row) => {
    setCandidates(prev => {
      if (prev.some(c => c.isbn===row.isbn && c.source===row.source && c.source_condition===row.source_condition)) return prev;
      const next = [{...row, addedAt: Date.now()}, ...prev];
      try { localStorage.setItem("tb_candidates", JSON.stringify(next)); } catch {}
      return next;
    });
  };
  const removeCandidate = (isbn, source, source_condition) => {
    setCandidates(prev => {
      const next = prev.filter(c => !(c.isbn===isbn && c.source===source && c.source_condition===source_condition));
      try { localStorage.setItem("tb_candidates", JSON.stringify(next)); } catch {}
      return next;
    });
  };
  const { toasts, push } = useToast();

  const [isbns, setIsbns] = useState([]);
  const [intervals, setIntervals] = useState({});
  const [status, setStatus] = useState(null);
  const schedTick = status?.sched_tick_seconds || 3600;
  const [alertStats, setAlertStats] = useState({});
  const [isbnAlertCounts, setIsbnAlertCounts] = useState({}); // {isbn: count} — geçmiş alert sayısı
  const [runState, setRunState] = useState({});
  const [loading, setLoading] = useState(true);
  const [backoffStatus, setBackoffStatus] = useState(null);

  // ── Watchlist drawer state ───────────────────────────────────────────────
  const [wlDrawerIsbn, setWlDrawerIsbn] = useState(null);
  const [wlDrawerData, setWlDrawerData] = useState(null);
  const [wlDrawerLoading, setWlDrawerLoading] = useState(false);
  const [wlSoldScrape, setWlSoldScrape] = useState({});        // { [isbn]: {loading,data,error} }
  const [wlLightbox, setWlLightbox] = useState(null);
  const _wlPrefetchCache = useRef({});

  const openWlDrawer = useCallback(async (isbn) => {
    setWlDrawerIsbn(isbn);
    const cached = _wlPrefetchCache.current[isbn];
    if (cached) { setWlDrawerData(cached); setWlDrawerLoading(false); return; }
    setWlDrawerData(null);
    setWlDrawerLoading(true);
    try {
      const d = await req(`/alerts/details?isbn=${isbn}`, {}, 20000);
      _wlPrefetchCache.current[isbn] = d;
      setWlDrawerData(d);
    } catch(e) {
      setWlDrawerData({ ok: false, error: e.message });
    } finally {
      setWlDrawerLoading(false);
    }
  }, []);

  const fetchWlSoldScrape = async (isbn) => {
    setWlSoldScrape(s=>({...s,[isbn]:{loading:true,data:null,error:null}}));
    try {
      const d = await req(`/ebay/sold-avg/${isbn}`, {}, 25000);
      setWlSoldScrape(s=>({...s,[isbn]:{loading:false,data:d,error:null}}));
    } catch(e) {
      setWlSoldScrape(s=>({...s,[isbn]:{loading:false,data:null,error:e.message}}));
    }
  };

  const [wlBfData, setWlBfData] = useState({});
  const fetchWlBookfinder = async (isbn, condition = "all") => {
    setWlBfData(s=>({...s,[isbn]:{loading:true,data:null,error:null,condition}}));
    try {
      const d = await req(`/bookfinder/${isbn}?condition=${condition}`, {}, 35000);
      setWlBfData(s=>({...s,[isbn]:{loading:false,data:d,error:null}}));
    } catch(e) {
      setWlBfData(s=>({...s,[isbn]:{loading:false,data:null,error:e.message}}));
    }
  };

  const [rules, setRules] = useState({});

  // Add Wizard state
  const [showWizard, setShowWizard] = useState(false);
  const [wizIsbn, setWizIsbn] = useState("");
  const [isbnInputError, setIsbnInputError] = useState("");
  const [wizNewMax, setWizNewMax] = useState("");
  const [wizUsedMax, setWizUsedMax] = useState("");
  const [wizInterval, setWizInterval] = useState("4h");
  const [wizAdding, setWizAdding] = useState(false);
  // Persist last-used values across wizard opens (survives tab switch, not page reload)
  const wizDefaults = { newMax: 50, usedMax: 30, interval: "4h" };
  const lastUsed = { newMax: parseInt(wizNewMax)||wizDefaults.newMax, usedMax: parseInt(wizUsedMax)||wizDefaults.usedMax, interval: wizInterval||wizDefaults.interval };

  // Inline rule edit
  const [editingRule, setEditingRule] = useState(null); // isbn or null
  const [editRuleNewMax, setEditRuleNewMax] = useState("");
  const [editRuleUsedMax, setEditRuleUsedMax] = useState("");

  const [newIsbn, setNewIsbn] = useState("");
  const [newInterval, setNewInterval] = useState("4h");
  const [editing, setEditing] = useState(null);
  const [editVal, setEditVal] = useState("");
  const [isbnFilter, setIsbnFilter] = useState("");
  const [csvText, setCsvText] = useState("");
  const [csvImporting, setCsvImporting] = useState(false);
  const [showCsvImport, setShowCsvImport] = useState(false);

  const load = useCallback(async () => {
    try {
      const [a,b,c,d,e,f] = await Promise.allSettled([req("/isbns"),req("/rules"),req("/status"),req("/alerts/stats"),req("/run-state"),req("/alerts/summary")]);
      if (a.status==="fulfilled") {
        const loaded = a.value.items||[];
        setIsbns(loaded);
        // Background pre-warm: watchlist drawer cache (staggered, after 2s)
        loaded.slice(0,8).forEach((isbn,i)=>{
          if(_wlPrefetchCache.current[isbn]) return;
          setTimeout(async()=>{
            try{ const d2=await req(`/alerts/details?isbn=${isbn}`,{},20000); if(d2?.ok) _wlPrefetchCache.current[isbn]=d2; }catch{}
          }, 2000 + i*500);
        });
      }
      if (b.status==="fulfilled") {
        setIntervals(b.value.intervals||{});
        setRules(b.value.rules||{});
      }
      if (c.status==="fulfilled") setStatus(c.value);
      if (d.status==="fulfilled") setAlertStats(d.value.stats||{});
      if (e.status==="fulfilled") setRunState(e.value.by_isbn||{});
      if (f.status==="fulfilled") setIsbnAlertCounts(f.value.by_isbn||{});
      /* Finding API backoff — deprecated, no longer fetched */
    } catch(err) { push("Yüklenirken hata: "+err.message,"error"); }
    finally { setLoading(false); }
  }, [push]);

  useEffect(()=>{ load(); const t=setInterval(load,30000); return()=>clearInterval(t); },[load]);

  const addIsbn = async () => {
    const isbn=newIsbn.trim(); if(!isbn) return;
    try {
      const secs=newInterval?parseSecs(newInterval):null;
      const res=await req("/isbns",{method:"POST",body:JSON.stringify({isbn})});
      if(res.added){ setIsbns(p=>[...p,isbn]); if(secs){await req(`/rules/${isbn}/interval`,{method:"PUT",body:JSON.stringify({interval_seconds:secs})}); setIntervals(p=>({...p,[isbn]:secs}));} push(`${isbn} eklendi`,"success"); }
      else push(`${isbn} zaten listede`,"info");
      setNewIsbn(""); setNewInterval("4h");
    } catch(e){ push("Eklenemedi: "+e.message,"error"); }
  };

  const deleteIsbn = async (isbn) => {
    try { await req(`/isbns/${isbn}`,{method:"DELETE"}); setIsbns(p=>p.filter(i=>i!==isbn)); push(`${isbn} silindi`,"success"); }
    catch(e){ push("Silinemedi: "+e.message,"error"); }
  };

  const saveInterval = async (isbn, val) => {
    try {
      const secs=val.trim()?parseSecs(val):null;
      if(val.trim()&&!secs){push("Geçersiz format (örn: 4h, 30m, 1d)","error");return;}
      if(secs){ await req(`/rules/${isbn}/interval`,{method:"PUT",body:JSON.stringify({interval_seconds:secs})}); setIntervals(p=>({...p,[isbn]:secs})); push(`Interval: ${fmtSecs(secs)}`,"success"); }
      else { setIntervals(p=>{const r={...p};delete r[isbn];return r;}); push("Varsayılana döndürüldü","info"); }
      setEditing(null);
    } catch(e){ push("Ayarlanamadı: "+e.message,"error"); }
  };

  const submitWizard = async () => {
    const isbn = wizIsbn.trim();
    if (!isbn) return;
    setWizAdding(true);
    try {
      const res = await req("/isbns", {method:"POST", body:JSON.stringify({isbn})});
      const canonical = res.isbn;
      if (!res.added && res.isbn) {
        push(`${canonical} zaten listede`, "info");
      }
      // Always set limits if provided
      if (wizNewMax || wizUsedMax) {
        const nm = wizNewMax ? Number(wizNewMax) : undefined;
        const um = wizUsedMax ? Number(wizUsedMax) : undefined;
        await req(`/rules/${canonical}/override`, {method:"PUT", body:JSON.stringify({new_max: nm, used_all_max: um})});
      }
      const secs = wizInterval ? parseSecs(wizInterval) : null;
      if (secs) {
        await req(`/rules/${canonical}/interval`, {method:"PUT", body:JSON.stringify({interval_seconds: secs})});
      }
      if (res.added) push(`${canonical} eklendi`, "success");
      setShowWizard(false);
      setWizIsbn(""); setWizNewMax(""); setWizUsedMax(""); setWizInterval("4h");
      setNewIsbn(""); setIsbnInputError("");
      load();
    } catch(e) { push("Eklenemedi: " + e.message, "error"); }
    finally { setWizAdding(false); }
  };

  const saveRuleLimits = async (isbn) => {
    try {
      const nm = editRuleNewMax ? Number(editRuleNewMax) : undefined;
      const um = editRuleUsedMax ? Number(editRuleUsedMax) : undefined;
      await req(`/rules/${isbn}/override`, {method:"PUT", body:JSON.stringify({new_max: nm, used_all_max: um})});
      setRules(p => ({...p, [isbn]: {...(p[isbn]||{}), new_max: nm, used_all_max: um}}));
      push(`Limitler güncellendi`, "success");
      setEditingRule(null);
    } catch(e) { push("Güncellenemedi: " + e.message, "error"); }
  };

  const clearAlerts = async (isbn) => {
    try { await req(`/alerts/${isbn}`,{method:"DELETE"}); setIsbnAlertCounts(p=>{const r={...p};delete r[isbn];return r;}); push("Alertler temizlendi","success"); }
    catch(e){ push("Temizlenemedi: "+e.message,"error"); }
  };

  const importCsv = async () => {
    if (!csvText.trim()) return;
    setCsvImporting(true);
    try {
      const res = await req("/isbns/import", {method:"POST", body:JSON.stringify({csv_text: csvText})});
      push(`${res.added} ISBN eklendi, ${res.skipped_duplicates} zaten vardı`, "success");
      if (res.errors?.length) push(`Uyarı: ${res.errors[0]}`, "error");
      setCsvText(""); setShowCsvImport(false); load();
    } catch(e){ push("Import hatası: "+e.message,"error"); }
    finally { setCsvImporting(false); }
  };

  const totalAlerts=Object.values(alertStats).reduce((s,n)=>s+n,0);
  const inp={background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.text};
  const row={background:C.rowBg,border:`1px solid ${C.rowBorder}`};
  const bookMeta = useBookMeta(isbns);
  const titles = titlesFromMeta(bookMeta);

  return (
    <div style={{fontFamily:"var(--mono)",background:C.bg,minHeight:"100vh",color:C.text,transition:"background .25s,color .25s"}}>
      <style>{buildCss(C)}</style>
      <ToastStack toasts={toasts} C={C}/>

      {/* Header */}
      <div style={{background:C.surface,borderBottom:`1px solid ${C.border}`,padding:"0 32px",position:"sticky",top:0,zIndex:10}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",height:56}}>
          <div style={{display:"flex",alignItems:"center",gap:12}}>
            <div style={{width:28,height:28,background:"linear-gradient(135deg,#f0a500,#ff6b35)",borderRadius:6,display:"flex",alignItems:"center",justifyContent:"center",fontSize:14}}>📦</div>
            <span style={{fontFamily:"var(--sans)",fontWeight:600,fontSize:15}}>TrackerBundle</span>
            <span style={{color:C.muted3}}>/</span>
            <span style={{color:C.muted,fontSize:12}}>eBay Panel</span>
            <span style={{fontSize:9,color:C.muted3,letterSpacing:"0.05em",background:C.surface2,border:`1px solid ${C.border}`,borderRadius:3,padding:"1px 5px",fontFamily:"var(--mono)",display:"flex",flexDirection:"column",gap:0,lineHeight:"14px"}}>
  <span>{BUILD_ID}</span>
  {BUILD_TIME&&<span style={{color:C.muted3,fontSize:8}}>{BUILD_TIME}</span>}
</span>
          </div>
          <div style={{display:"flex",alignItems:"center",gap:12}}>
            {status?.ok
              ? <><span style={{width:7,height:7,borderRadius:"50%",background:C.green,boxShadow:`0 0 6px ${C.green}`,display:"inline-block"}}/><span style={{fontSize:11,color:C.green,letterSpacing:"0.05em"}}>LIVE</span></>
              : <><span style={{width:7,height:7,borderRadius:"50%",background:C.red,display:"inline-block"}}/><span style={{fontSize:11,color:C.red}}>OFFLINE</span></>}
            {status&&<span style={{fontSize:11,color:C.muted3}}>{new Date(status.time_utc).toLocaleTimeString("tr-TR")}</span>}
            <button className="icon-btn" onClick={load} title="Yenile">↻</button>
            <button
              onClick={()=>setTheme(t=>t==="dark"?"light":t==="light"?"soft":"dark")}
              style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:6,cursor:"pointer",padding:"5px 10px",fontSize:15,color:C.text,transition:"all .2s"}}
              title={theme==="dark"?"Açık temaya geç":theme==="light"?"Soft temaya geç":"Koyu temaya geç"}>
              {theme==="dark"?"☀️":theme==="light"?"🌤":"🌙"}
            </button>
            <button onClick={()=>setBlueFilter(f=>!f)}
              style={{background:blueFilter?"rgba(251,191,36,.15)":C.surface2,border:`1px solid ${blueFilter?"#fbbf24":C.border}`,borderRadius:6,cursor:"pointer",padding:"5px 10px",fontSize:15,color:blueFilter?"#fbbf24":C.muted2,transition:"all .2s"}}
              title={blueFilter?"🟡 Mavi ışık filtresi AÇIK — kapatmak için tıkla":"💡 Mavi ışık filtresi — göz yorgunluğunu azaltır"}>
              {blueFilter?"🟡":"💡"}
            </button>
          </div>
        </div>
        <div style={{display:"flex"}}>
          {TABS.map(t=>{
            const isScanning = t==="discover" && scanJob?.scanning;
            const discoverLabel = isScanning ? `⏳ ${scanJob?.progress?.done||0}/${scanJob?.progress?.total||0}` : (candidates.length>0 ? `🔍 Discover ⭐${candidates.length}` : "🔍 Discover"); const label = {dashboard:"📊 Dashboard",watchlist:"👁 Watchlist",discover:discoverLabel,alerts:"🔔 Alerts",pricing:"💰 Pricing",bookstores:"🏪 Bookstores"}[t]||t;
            return <button key={t} className="tab-btn" onClick={()=>setTab(t)} style={{padding:"10px 20px",fontSize:12,color:tab===t?C.accent:C.muted,borderBottom:tab===t?`2px solid ${C.accent}`:"2px solid transparent",fontWeight:tab===t?600:400,letterSpacing:"0.01em"}}>{label}</button>;
          })}
        </div>
      </div>

      {/* Content */}
      <div style={{padding:"20px 24px"}}>
        {loading ? <div style={{color:C.muted3,textAlign:"center",paddingTop:80,fontSize:13}}>Yükleniyor…</div> : (
          <>
            {tab==="dashboard"&&(
              <div>
                <div style={{marginBottom:10,color:C.muted3,fontSize:11,letterSpacing:"0.01em"}}>
                  Overview · {new Date().toLocaleDateString("tr-TR",{day:"numeric",month:"long",year:"numeric"})}
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:16}}>
                  <StatCard C={C} icon="📚" label="Toplam ISBN" value={isbns.length} sub="watchlist'te" accent={C.accent}/>
                  <StatCard C={C} icon="🔄" label="Tarama Yapıldı" value={Object.keys(runState).length} sub="run_state kayıtları" accent={C.blue}/>
                  <StatCard C={C} icon="🎯" label="Toplam Alert" value={totalAlerts} sub="benzersiz item" accent={C.green}/>
                  <StatCard C={C} icon="🔔" label="Bot Token" value={status?.has_bot_token?"✓":"✗"} sub={status?.has_bot_token?"Telegram aktif":"Token yok"} accent={status?.has_bot_token?C.green:C.red}/>
                </div>

                {/* Finding API Backoff — removed (Finding API deprecated) */}
                {(alertStats.active_keys>0||alertStats.total_keys>0)&&(
                  <div style={{marginBottom:24}}>
                    <ST C={C}>Dedup Durumu</ST>
                    <div style={{display:"flex",gap:12}}>
                      <div style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 14px"}}>
                        <div style={{fontSize:10,color:C.muted}}>Aktif</div>
                        <div style={{fontSize:18,fontWeight:700,color:C.green}}>{alertStats.active_keys||0}</div>
                      </div>
                      <div style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 14px"}}>
                        <div style={{fontSize:10,color:C.muted}}>Toplam</div>
                        <div style={{fontSize:18,fontWeight:700,color:C.text}}>{alertStats.total_keys||0}</div>
                      </div>
                    </div>
                  </div>
                )}
                <ST C={C}>Watchlist Önizleme</ST>
                {isbns.slice(0,5).map(isbn=>(
                  <div key={isbn} className="row-item" style={{...row, gap:10}}>
                    <div style={{flexShrink:0,width:28,height:38,borderRadius:3,overflow:"hidden",background:C.surface2}}>
                      <img src={`https://covers.openlibrary.org/b/isbn/${isbn}-S.jpg`}
                        loading="lazy" alt=""
                        style={{width:"100%",height:"100%",objectFit:"cover",display:"block"}}
                        onError={e=>{e.target.style.opacity="0";}}/>
                    </div>
                    <div style={{flex:1,display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                      <span style={{fontSize:13}}>{isbn}</span>
                      {titles[isbn]&&<span style={{fontSize:11,color:C.muted,fontFamily:"var(--sans)"}}>— {titles[isbn]}</span>}
                    </div>
                    <span style={{fontSize:10,color:C.muted}}>interval: {fmtSecs(intervals[isbn])}</span>
                    {runState[isbn]&&<span style={{fontSize:10,color:C.muted2}}>son: {fmtTime(runState[isbn])}</span>}
                    {isbnAlertCounts[isbn]>0&&<span className="badge" style={{background:theme==="dark"?"#1a2a1a":theme==="soft"?"#d4ede1":"#f0fdf4",color:C.green,fontSize:10}}>🎯 {isbnAlertCounts[isbn]}</span>}
                    <span style={{width:8,height:8,borderRadius:"50%",background:C.green,display:"inline-block"}}/>
                  </div>
                ))}
                {isbns.length>5&&<div style={{fontSize:11,color:C.muted3,marginTop:8}}>+{isbns.length-5} daha</div>}

                {/* ── Görünüm Ayarları ───────────────────────────────────── */}
                <div style={{marginTop:24,marginBottom:10}}>
                  <ST C={C}>Görünüm & Sistem</ST>
                </div>
                <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:14}}>
                  {/* Theme */}
                  <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"14px 16px"}}>
                    <div style={{fontSize:11,fontWeight:600,color:C.accent,letterSpacing:"0.07em",textTransform:"uppercase",marginBottom:10}}>Tema</div>
                    <div style={{display:"flex",gap:6}}>
                      {["dark","soft","light"].map(id=>(
                        <button key={id} onClick={()=>setTheme(id)} style={{
                          flex:1,padding:"6px 0",borderRadius:6,cursor:"pointer",
                          fontFamily:"var(--mono)",fontSize:11,
                          background:theme===id?C.accent:C.bg,
                          color:theme===id?"#fff":C.muted,
                          border:`1px solid ${theme===id?C.accent:C.border}`,
                          fontWeight:theme===id?600:400,transition:"all .15s",
                        }}>{id==="dark"?"🌙 Dark":id==="soft"?"🌤 Soft":"☀️ Light"}</button>
                      ))}
                    </div>
                    <div style={{marginTop:10,display:"flex",alignItems:"center",justifyContent:"space-between"}}>
                      <span style={{fontSize:11,color:C.muted}}>Blue-light filtre</span>
                      <button onClick={()=>setBlueFilter(b=>!b)} style={{
                        padding:"4px 12px",borderRadius:6,cursor:"pointer",
                        fontFamily:"var(--mono)",fontSize:11,
                        background:blueFilter?C.accent:C.bg,
                        color:blueFilter?"#fff":C.muted,
                        border:`1px solid ${blueFilter?C.accent:C.border}`,
                        transition:"all .15s",
                      }}>{blueFilter?"ON":"OFF"}</button>
                    </div>
                  </div>
                  {/* System info */}
                  <div style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:10,padding:"14px 16px"}}>
                    <div style={{fontSize:11,fontWeight:600,color:C.accent,letterSpacing:"0.07em",textTransform:"uppercase",marginBottom:10}}>Sistem</div>
                    <div style={{display:"flex",flexDirection:"column",gap:6}}>
                      <div style={{display:"flex",justifyContent:"space-between",fontSize:11}}>
                        <span style={{color:C.muted}}>Build</span>
                        <span style={{color:C.text,fontFamily:"var(--mono)",fontSize:10}}>{BUILD_ID}{BUILD_TIME&&<span style={{color:C.muted,fontSize:9,marginLeft:6}}>{BUILD_TIME}</span>}</span>
                      </div>
                      <div style={{display:"flex",justifyContent:"space-between",fontSize:11}}>
                        <span style={{color:C.muted}}>API</span>
                        <span style={{color:C.text,fontFamily:"var(--mono)",fontSize:10}}>{BASE||"same origin"}</span>
                      </div>
                      <div style={{display:"flex",justifyContent:"space-between",fontSize:11}}>
                        <span style={{color:C.muted}}>Telegram</span>
                        <span style={{color:status?.has_bot_token?C.green:"#ef4444",fontSize:10}}>{status?.has_bot_token?"✓ Aktif":"✗ Token yok"}</span>
                      </div>
                      <a href="/llm/status" target="_blank" rel="noopener"
                        style={{marginTop:4,fontSize:11,color:C.accent,textDecoration:"none"}}>
                        🤖 LLM Provider Durumu →
                      </a>
                      <a href="/buyback/test" target="_blank" rel="noopener"
                        style={{fontSize:11,color:C.accent,textDecoration:"none"}}>
                        💰 Buyback API Test →
                      </a>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {tab==="watchlist"&&(
              <div>
                {/* Add Wizard Modal */}
                {showWizard && (
                  <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,.6)",zIndex:100,display:"flex",alignItems:"center",justifyContent:"center"}} onClick={e=>{if(e.target===e.currentTarget){setShowWizard(false); setWizIsbn(""); setNewIsbn(""); setIsbnInputError("");}}}>
                    <div
                      style={{background:C.surface,border:`1px solid ${C.border}`,borderRadius:14,padding:32,width:480,maxWidth:"95vw",boxShadow:"0 20px 60px rgba(0,0,0,.4)"}}
                      onKeyDown={e=>{
                        if (e.key==="Enter" && !wizAdding && wizIsbn.trim()) submitWizard();
                        if (e.key==="Escape") { setShowWizard(false); setWizIsbn(""); setNewIsbn(""); setIsbnInputError(""); }
                      }}
                    >
                      <div style={{fontSize:15,fontWeight:600,color:C.text,marginBottom:4}}>📚 Limit & Aralık Ayarla</div>
                      <div style={{fontSize:12,color:C.muted3,marginBottom:20,fontFamily:"var(--mono)"}}>
                        ISBN: <b style={{color:C.accent}}>{wizIsbn}</b>
                      </div>

                      {/* New Max slider */}
                      <div style={{marginBottom:20}}>
                        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                          <span style={{fontSize:11,color:C.muted}}>New Max</span>
                          <div style={{display:"flex",alignItems:"center",gap:6}}>
                            <span style={{fontSize:11,color:C.muted3}}>$</span>
                            <input
                              type="number" min={0} max={200} step={1}
                              value={wizNewMax}
                              autoFocus
                              onChange={e=>setWizNewMax(String(Math.min(200,Math.max(0,parseInt(e.target.value)||0))))}
                              style={{width:60,padding:"3px 6px",borderRadius:5,border:`1px solid ${C.green}`,background:C.inputBg,color:C.green,fontFamily:"var(--mono)",fontSize:14,fontWeight:600,textAlign:"right"}}
                            />
                          </div>
                        </div>
                        <input
                          type="range" min={0} max={200} step={1}
                          value={wizNewMax||0}
                          onChange={e=>setWizNewMax(e.target.value)}
                          style={{width:"100%",accentColor:C.green,cursor:"pointer"}}
                        />
                        <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:C.muted3,marginTop:2}}>
                          <span>$0</span><span>$50</span><span>$100</span><span>$150</span><span>$200</span>
                        </div>
                      </div>

                      {/* Used Good Max slider */}
                      <div style={{marginBottom:20}}>
                        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                          <span style={{fontSize:11,color:C.muted}}>Used Good Max</span>
                          <div style={{display:"flex",alignItems:"center",gap:6}}>
                            <span style={{fontSize:11,color:C.muted3}}>$</span>
                            <input
                              type="number" min={0} max={100} step={1}
                              value={wizUsedMax}
                              onChange={e=>setWizUsedMax(String(Math.min(100,Math.max(0,parseInt(e.target.value)||0))))}
                              style={{width:60,padding:"3px 6px",borderRadius:5,border:`1px solid ${C.accent}`,background:C.inputBg,color:C.accent,fontFamily:"var(--mono)",fontSize:14,fontWeight:600,textAlign:"right"}}
                            />
                          </div>
                        </div>
                        <input
                          type="range" min={0} max={100} step={1}
                          value={wizUsedMax||0}
                          onChange={e=>setWizUsedMax(e.target.value)}
                          style={{width:"100%",accentColor:C.accent,cursor:"pointer"}}
                        />
                        <div style={{display:"flex",justifyContent:"space-between",fontSize:9,color:C.muted3,marginTop:2}}>
                          <span>$0</span><span>$25</span><span>$50</span><span>$75</span><span>$100</span>
                        </div>
                        {wizUsedMax>0 && (
                          <div style={{marginTop:8,fontSize:10,color:C.muted3,lineHeight:1.9,display:"flex",gap:12,flexWrap:"wrap"}}>
                            <span>Like New <b style={{color:C.blue}}>${Math.round(Number(wizUsedMax)*1.15)}</b></span>
                            <span>Very Good <b style={{color:C.purple}}>${Math.round(Number(wizUsedMax)*1.10)}</b></span>
                            <span>Acceptable <b style={{color:C.orange}}>${Math.round(Number(wizUsedMax)*0.80)}</b></span>
                          </div>
                        )}
                      </div>

                      {/* Interval */}
                      <div style={{marginBottom:24}}>
                        <div style={{fontSize:11,color:C.muted,marginBottom:6}}>Tarama Aralığı</div>
                        <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                          {[["30m","30dk"],["1h","1s"],["4h","4s"],["8h","8s"],["12h","12s"],["24h","1g"],["48h","2g"]].map(([v,l])=>(
                            <button key={v} onClick={()=>setWizInterval(v)} style={{
                              padding:"5px 12px",borderRadius:5,fontFamily:"var(--mono)",fontSize:11,cursor:"pointer",
                              background: wizInterval===v ? C.accent : "none",
                              color: wizInterval===v ? C.accentText : C.muted,
                              border: `1px solid ${wizInterval===v ? C.accent : C.border}`,
                              fontWeight: wizInterval===v ? 600 : 400,
                            }}>{l}</button>
                          ))}
                        </div>
                      </div>

                      <div style={{display:"flex",gap:10,alignItems:"center"}}>
                        <button className="add-btn" onClick={submitWizard} disabled={wizAdding||!wizIsbn.trim()} style={{flex:1}}>
                          {wizAdding ? "Ekleniyor…" : `+ Ekle — $${wizUsedMax} used / $${wizNewMax} new / ${wizInterval}`}
                        </button>
                        <button onClick={()=>{setShowWizard(false); setWizIsbn(""); setNewIsbn(""); setIsbnInputError("");}} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,color:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"8px 14px",cursor:"pointer"}}>Esc</button>
                      </div>
                      <div style={{marginTop:8,fontSize:10,color:C.muted3,textAlign:"center"}}>Enter ile kaydet · Esc ile kapat</div>
                    </div>
                  </div>
                )}

                {/* Direct ISBN Input — always visible */}
                <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:20,marginBottom:16}}> 
                  <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap"}}>
                    <div style={{position:"relative",flex:1,minWidth:220}}>
                      <input
                        id="isbn-direct-input"
                        className="inp"
                        placeholder="978-0974769431 — ISBN yaz, Enter'a bas"
                        value={newIsbn}
                        onChange={e => {
                          setNewIsbn(e.target.value);
                          setIsbnInputError("");
                        }}
                        onKeyDown={e => {
                          if (e.key !== "Enter") return;
                          const raw = newIsbn.trim();
                          if (!raw) return;
                          const cleaned = cleanIsbn(raw);
                          if (!validateIsbn(cleaned)) {
                            setIsbnInputError("Geçersiz ISBN — kontrol et (10 veya 13 hane, checksum)");
                            return;
                          }
                          setIsbnInputError("");
                          setWizIsbn(cleaned);
                          // Keep existing values (last-used); only reset if truly empty
                          if (!wizNewMax) setWizNewMax(String(wizDefaults.newMax));
                          if (!wizUsedMax) setWizUsedMax(String(wizDefaults.usedMax));
                          if (!wizInterval) setWizInterval(wizDefaults.interval);
                          setShowWizard(true);
                        }}
                        style={{
                          width:"100%",
                          background:C.inputBg,
                          border:`1px solid ${isbnInputError ? C.red : C.inputBorder}`,
                          color:C.text,
                          paddingRight: 36,
                        }}
                      />
                      {newIsbn && (
                        <span style={{
                          position:"absolute", right:10, top:"50%", transform:"translateY(-50%)",
                          fontSize:11, color: validateIsbn(cleanIsbn(newIsbn)) ? C.green : C.muted3,
                          pointerEvents:"none", userSelect:"none",
                        }}>
                          {validateIsbn(cleanIsbn(newIsbn)) ? "✓" : "?"}
                        </span>
                      )}
                    </div>
                    {isbnInputError && (
                      <div style={{width:"100%",fontSize:11,color:C.red,marginTop:4,order:10}}>
                        ⚠ {isbnInputError}
                      </div>
                    )}
                    <button
                      className="add-btn"
                      onClick={() => {
                        const raw = newIsbn.trim();
                        if (!raw) { document.getElementById("isbn-direct-input")?.focus(); return; }
                        const cleaned = cleanIsbn(raw);
                        if (!validateIsbn(cleaned)) { setIsbnInputError("Geçersiz ISBN"); return; }
                        setIsbnInputError("");
                        setWizIsbn(cleaned);
                        if (!wizNewMax) setWizNewMax(String(wizDefaults.newMax));
                        if (!wizUsedMax) setWizUsedMax(String(wizDefaults.usedMax));
                        if (!wizInterval) setWizInterval(wizDefaults.interval);
                        setShowWizard(true);
                      }}
                      title="ISBN ekle (Enter ile de açılır)"
                    >
                      + Ekle
                    </button>
                    <button
                      onClick={()=>setShowCsvImport(p=>!p)}
                      style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:"pointer"}}
                    >
                      {showCsvImport ? "▲ CSV Kapat" : "📄 Toplu CSV"}
                    </button>
                  </div>
                </div>

                {/* CSV Toplu Import */}
                {showCsvImport && (
                  <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:20,marginBottom:16}}>
                    <ST C={C} style={{marginBottom:8}}>Toplu CSV Import</ST>
                    <div style={{fontSize:10,color:C.muted3,marginBottom:10,lineHeight:1.7}}>
                      Başlık satırı zorunlu: <code style={{color:C.accent}}>isbn,new_max,used_all_max,interval</code><br/>
                      Örn: <code style={{color:C.muted}}>9780132350884,50,30,4h</code> (boş hücreler varsayılan kullanır)
                    </div>
                    <textarea
                      className="inp"
                      rows={6}
                      placeholder={"isbn,new_max,used_all_max,interval\n9780132350884,50,30,4h\n9780974769431,,25,"}
                      value={csvText}
                      onChange={e=>setCsvText(e.target.value)}
                      style={{...inp,width:"100%",fontFamily:"var(--mono)",fontSize:11,resize:"vertical"}}
                    />
                    <div style={{display:"flex",gap:10,marginTop:10}}>
                      <button className="add-btn" onClick={importCsv} disabled={csvImporting||!csvText.trim()}>
                        {csvImporting ? "İçe aktarılıyor…" : "⬆ İçe Aktar"}
                      </button>
                      <button onClick={()=>{setCsvText("");setShowCsvImport(false);}} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,color:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"6px 14px",cursor:"pointer"}}>İptal</button>
                    </div>
                  </div>
                )}

                {/* Arama + Filtre */}
                <div style={{display:"flex",alignItems:"center",gap:10,marginBottom:12}}>
                  <input
                    className="inp"
                    placeholder="ISBN ara…"
                    value={isbnFilter}
                    onChange={e=>setIsbnFilter(e.target.value)}
                    style={{...inp,width:220,fontSize:12}}
                  />
                  <span style={{fontSize:11,color:C.muted}}>
                    {isbnFilter ? `${isbns.filter(i=>i.includes(isbnFilter.replace(/-/g,""))).length} / ${isbns.length}` : `${isbns.length} ISBN izleniyor`}
                  </span>
                </div>

                {isbns.length===0
                  ? <div style={{border:`1px dashed ${C.border}`,borderRadius:8,padding:32,textAlign:"center",color:C.muted3,fontSize:12}}>Henüz ISBN yok.</div>
                  : isbns
                      .filter(isbn => !isbnFilter || isbn.includes(isbnFilter.replace(/-/g,"")))
                      .map(isbn=>(
                    <div key={isbn} style={{...row,borderRadius:8,marginBottom:8,overflow:"hidden"}}>
                      <div className="row-item"
                        onClick={()=>{ if(!editingRule && !editing) openWlDrawer(isbn); }}
                        style={{borderBottom:editingRule===isbn?`1px solid ${C.border}`:"none",marginBottom:0,paddingBottom:editingRule===isbn?12:undefined,cursor:(editingRule||editing)?"default":"pointer",transition:"background .12s",gap:10}}
                      >
                        {/* Mini kapak */}
                        <div onClick={e=>e.stopPropagation()} style={{flexShrink:0,width:32,height:44,borderRadius:3,overflow:"hidden",background:C.surface2,cursor:"default"}}>
                          <img
                            src={`https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg`}
                            loading="lazy"
                            style={{width:"100%",height:"100%",objectFit:"cover",display:"block"}}
                            onError={e=>{e.target.style.opacity="0";}}
                            alt=""
                          />
                        </div>
                        <div style={{flex:1}}>
                          <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                            <span style={{fontFamily:"var(--sans)",fontSize:13,fontWeight:600}}>{isbn}</span>
                            {titles[isbn]&&<span style={{fontSize:12,color:C.muted,fontFamily:"var(--sans)"}}>— {titles[isbn]}</span>}
                            {titles[isbn]===null&&<span style={{fontSize:10,color:C.muted3}}>…</span>}
                            {bookMeta[isbn]?.author&&<span style={{fontSize:10,color:C.muted3,fontFamily:"var(--sans)"}}>{bookMeta[isbn].author}{bookMeta[isbn].year?` · ${bookMeta[isbn].year}`:""}</span>}
                            {isbnAlertCounts[isbn]>0&&<span className="badge" style={{background:theme==="dark"?"#1a2a1a":theme==="soft"?"#d4ede1":"#f0fdf4",color:C.green}}>🎯 {isbnAlertCounts[isbn]}</span>}
                          </div>
                          <div style={{fontSize:10,color:C.muted2,marginTop:3,display:"flex",gap:12}}>
                            <span>{runState[isbn]?`📡 ${fmtTime(runState[isbn])}`:"🕐 henüz taranmadı"}</span>
                            {rules[isbn]?.new_max!=null&&<span style={{color:C.green}}>New: ${rules[isbn].new_max}</span>}
                            {rules[isbn]?.used_all_max!=null&&<span style={{color:C.accent}}>Used: ${rules[isbn].used_all_max}</span>}
                          </div>
                        </div>
                        <button onClick={()=>{
                          if(editingRule===isbn){setEditingRule(null);}
                          else{setEditingRule(isbn);setEditRuleNewMax(rules[isbn]?.new_max||"");setEditRuleUsedMax(rules[isbn]?.used_all_max||"");}
                        }} style={{background:C.surface2,border:`1px solid ${editingRule===isbn?C.accent:C.border}`,borderRadius:4,color:editingRule===isbn?C.accent:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"3px 10px",cursor:"pointer"}} title="Limitleri düzenle">
                          {editingRule===isbn?"✕ Kapat":"✏ Limit"}
                        </button>
                        {editing===isbn ? (
                          <div style={{display:"flex",gap:6,alignItems:"center"}}>
                            <input className="inp" style={{...inp,width:90,padding:"4px 8px",fontSize:12}} placeholder="4h / 30m" value={editVal} autoFocus onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")saveInterval(isbn,editVal);if(e.key==="Escape")setEditing(null);}}/>
                            <button className="add-btn" style={{padding:"4px 10px",fontSize:12}} onClick={()=>saveInterval(isbn,editVal)}>✓</button>
                            <button className="icon-btn" style={{fontSize:13}} onClick={()=>setEditing(null)}>✕</button>
                          </div>
                        ) : (
                          <button onClick={()=>{setEditing(isbn);setEditVal(fmtSecs(intervals[isbn])||"");}} style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:4,color:intervals[isbn]?C.blue:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"3px 10px",cursor:"pointer"}} title={intervals[isbn]?"Özel interval":"Global interval (ayarlardan)"}>
                            ⏱ {fmtSecs(intervals[isbn]) || fmtSecs(schedTick) || "—"}
                          </button>
                        )}
                        <a
                          href={buildEbaySearchUrl({ isbn })}
                          target="_blank" rel="noreferrer"
                          title="eBay'de en ucuzdan · Shift+Tık = link bozuk bildir"
                          onClick={async e=>{
                            if (e.shiftKey) {
                              e.preventDefault();
                              await reportBrokenLink({ isbn, url: buildEbaySearchUrl({ isbn }), context: "watchlist" });
                              alert("Link sorunu kaydedildi.");
                            }
                          }}
                          style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:4,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"3px 10px",cursor:"pointer",textDecoration:"none",whiteSpace:"nowrap"}}>
                          eBay ↗
                        </a>
                        <button className="icon-btn" onClick={()=>deleteIsbn(isbn)} style={{color:C.muted2,fontSize:18}}>×</button>
                      </div>
                      {editingRule===isbn && (
                        <div style={{padding:"12px 16px",background:C.surface2,display:"flex",gap:10,alignItems:"flex-end",flexWrap:"wrap"}}>
                          <div>
                            <div style={{fontSize:10,color:C.muted,marginBottom:4}}>New Max ($)</div>
                            <input className="inp" type="number" placeholder="örn: 50" value={editRuleNewMax} onChange={e=>setEditRuleNewMax(e.target.value)} style={{width:100,padding:"5px 8px",fontSize:12,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.green}}/>
                          </div>
                          <div>
                            <div style={{fontSize:10,color:C.muted,marginBottom:4}}>Used Good Max ($)</div>
                            <input className="inp" type="number" placeholder="örn: 30" value={editRuleUsedMax} onChange={e=>setEditRuleUsedMax(e.target.value)} style={{width:100,padding:"5px 8px",fontSize:12,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.accent}}/>
                          </div>
                          {editRuleUsedMax && (
                            <div style={{fontSize:10,color:C.muted3,lineHeight:1.8}}>
                              Like New: <span style={{color:C.blue}}>${Math.round(Number(editRuleUsedMax)*1.15)}</span>{" · "}
                              VG: <span style={{color:C.purple}}>${Math.round(Number(editRuleUsedMax)*1.10)}</span>{" · "}
                              Acceptable: <span style={{color:C.orange}}>${Math.round(Number(editRuleUsedMax)*0.80)}</span>
                            </div>
                          )}
                          <button className="add-btn" style={{padding:"6px 18px",fontSize:12}} onClick={()=>saveRuleLimits(isbn)}>✓ Kaydet</button>
                        </div>
                      )}
                    </div>
                  ))}
              </div>
            )}

            {tab==="pricing"&&<PricingTab isbns={isbns} C={C} push={push} titles={titles} rules={rules} onRulesSaved={load}/>}

            {/* Watchlist lightbox */}
            {wlLightbox && (
              <div onClick={()=>setWlLightbox(null)} style={{position:"fixed",inset:0,zIndex:200,background:"rgba(0,0,0,.85)",display:"flex",alignItems:"center",justifyContent:"center",cursor:"zoom-out"}}>
                <img src={wlLightbox} alt="" style={{maxWidth:"90vw",maxHeight:"90vh",objectFit:"contain",borderRadius:8}}/>
                <button onClick={()=>setWlLightbox(null)} style={{position:"absolute",top:20,right:24,background:"none",border:"none",color:"white",fontSize:28,cursor:"pointer"}}>×</button>
              </div>
            )}
            {/* Watchlist drawer */}
            {wlDrawerIsbn && (
              <DetailDrawer
                isbn={wlDrawerIsbn}
                alertEntry={null}
                drawerData={wlDrawerData}
                drawerLoading={wlDrawerLoading}
                soldScrape={wlSoldScrape[wlDrawerIsbn]}
                bfScrape={wlBfData[wlDrawerIsbn]}
                bookMeta={bookMeta}
                C={C}
                onClose={()=>{setWlDrawerIsbn(null);setWlDrawerData(null);}}
                onRetry={()=>openWlDrawer(wlDrawerIsbn)}
                onSoldFetch={fetchWlSoldScrape}
                onBfFetch={fetchWlBookfinder}
                onLightbox={setWlLightbox}
              />
            )}

{/* ── Global scan status bar — Discover dışındaki her tab'da görünür ── */}
            {scanJob?.scanning && tab !== "discover" && (
              <div style={{
                background: scanJob?.paused ? "#431407" : "#0c1a2e",
                borderRadius:10, marginBottom:16,
                border:`1px solid ${scanJob?.paused ? "#f97316" : "#2563eb"}`,
                overflow:"hidden",
              }}>
                {/* Progress bar track */}
                <div style={{height:3, background:"#1e3a5f"}}>
                  <div style={{
                    height:"100%",
                    background: scanJob?.paused ? "#f97316" : "#2563eb",
                    width:`${scanJob?.progress?.total>0 ? Math.round((scanJob?.progress?.done||0)/scanJob.progress.total*100) : 0}%`,
                    transition:"width 0.4s ease"
                  }}/>
                </div>
                <div style={{display:"flex", alignItems:"center", gap:10, padding:"8px 14px"}}>
                  <span style={{fontSize:12, fontWeight:700, flexShrink:0,
                    color: scanJob?.paused ? "#f97316" : "#60a5fa"}}>
                    {scanJob?.paused ? "⏸ Duraklatıldı" : "⏳ Taranıyor..."}
                  </span>
                  <span style={{fontSize:11, color:"#94a3b8", flex:1}}>
                    {(() => {
                      const done = scanJob?.progress?.done||0;
                      const total = scanJob?.progress?.total||0;
                      const pct = total>0 ? Math.round(done/total*100) : 0;
                      const acc = scanJob?.progress?.accepted_count||0;
                      const eta = scanJob?.progress?.eta_s;
                      const etaStr = eta && !scanJob?.paused ? (eta>60?` · ~${Math.ceil(eta/60)}dk`:` · ~${eta}s`) : "";
                      return `${done}/${total} · %${pct} · ✅ ${acc} fırsat${etaStr}`;
                    })()}
                  </span>
                  <button
                    onClick={async () => {
                      const jid = scanJob?.jobId;
                      if (!jid) return;
                      if (scanJob?.paused) {
                        await fetch("/discover/csv-arb/resume/" + jid, {method:"POST"});
                        setScanJob(p => ({...p, paused:false}));
                      } else {
                        await fetch("/discover/csv-arb/pause/" + jid, {method:"POST"});
                        setScanJob(p => ({...p, paused:true}));
                      }
                    }}
                    style={{flexShrink:0, padding:"4px 12px", fontSize:11, fontWeight:700,
                      background: scanJob?.paused ? "#16a34a22" : "#f9731622",
                      color: scanJob?.paused ? "#22c55e" : "#f97316",
                      border:`1px solid ${scanJob?.paused ? "#22c55e55" : "#f9731655"}`,
                      borderRadius:5, cursor:"pointer"}}>
                    {scanJob?.paused ? "▶ Devam Et" : "⏸ Duraklat"}
                  </button>
                  <button
                    onClick={async () => {
                      const jid = scanJob?.jobId;
                      if (!jid) return;
                      try {
                        const rd = await fetch("/discover/csv-arb/cancel/" + jid, {method:"POST"}).then(r=>r.json());
                        setScanJob(p => ({...p, scanning:false, paused:false,
                          results: rd.ok !== false ? {...rd, cancelled:true, partial:true} : p?.results}));
                      } catch(e) {
                        setScanJob(p => ({...p, scanning:false, paused:false}));
                      }
                      setTab("discover");
                    }}
                    style={{flexShrink:0, padding:"4px 12px", fontSize:11, fontWeight:700,
                      background:"#ef444422", color:"#ef4444",
                      border:"1px solid #ef444455",
                      borderRadius:5, cursor:"pointer"}}>
                    ⏹ Durdur
                  </button>
                  <button onClick={() => setTab("discover")}
                    style={{flexShrink:0, padding:"4px 12px", fontSize:11,
                      background:"transparent", color:"#60a5fa",
                      border:"1px solid #2563eb44",
                      borderRadius:5, cursor:"pointer"}}>
                    → Discover
                  </button>
                </div>
              </div>
            )}

{tab==="alerts"&&<AlertsFeedTab C={C} theme={theme} push={push} isbns={isbns} titles={titles} bookMeta={bookMeta}/>}
            {tab==="bookstores"&&<BookstoresTab C={C} theme={theme} push={push}/>}
            {tab==="discover"&&<DiscoverTab C={C} theme={theme} scanJob={scanJob} setScanJob={setScanJob} scanPollRef={scanPollRef} candidates={candidates} addCandidate={addCandidate} removeCandidate={removeCandidate} saveCandidates={saveCandidates} push={push} isbns={isbns} addIsbn={async(isbn,secs)=>{const res=await req("/isbns",{method:"POST",body:JSON.stringify({isbn})});if(res.added){setIsbns(p=>[...p,isbn]);if(secs){await req(`/rules/${isbn}/interval`,{method:"PUT",body:JSON.stringify({interval_seconds:secs})});}push(isbn+" watchlist'e eklendi","success");}else{push("Zaten watchlist'te","info");}}}/>}
            {/* candidates, history, settings are now sub-tabs inside Discover */}
          </>
        )}
      </div>
    </div>
  );
}

function buildCss(C) {
  return `
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=IBM+Plex+Sans:wght@400;600&display=swap');
    :root{--mono:'IBM Plex Mono','Courier New',monospace;--sans:'IBM Plex Sans',sans-serif;}
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
    ::-webkit-scrollbar{width:4px;} ::-webkit-scrollbar-track{background:${C.bg};} ::-webkit-scrollbar-thumb{background:${C.border};border-radius:2px;}
    .tab-btn{background:none;border:none;cursor:pointer;font-family:var(--mono);transition:all .15s;}
    .icon-btn{background:none;border:none;cursor:pointer;font-family:var(--mono);padding:4px 8px;border-radius:4px;transition:all .15s;color:${C.muted};}
    .icon-btn:hover{background:${C.surface2};color:${C.text};}
    .inp{background:${C.inputBg};border:1px solid ${C.inputBorder};color:${C.text};padding:8px 12px;border-radius:6px;font-family:var(--mono);font-size:13px;outline:none;transition:border .15s;}
    .inp:focus{border-color:${C.accent};}
    .add-btn{background:${C.accent};color:${C.accentText};border:none;padding:8px 20px;border-radius:6px;font-family:var(--mono);font-size:13px;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap;}
    .add-btn:hover:not(:disabled){background:${C.accentHover};transform:translateY(-1px);}
    .add-btn:disabled{opacity:0.4;cursor:not-allowed;}
    .row-item{border-radius:8px;padding:12px 16px;margin-bottom:8px;display:flex;align-items:center;gap:12px;transition:border-color .15s;}
    .row-item:hover{border-color:${C.accent}!important;}
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:500;}
    @keyframes slideIn{from{opacity:0;transform:translateX(10px);}to{opacity:1;transform:translateX(0);}}
    @keyframes spin{from{transform:rotate(0deg);}to{transform:rotate(360deg);}}
  `;
}
