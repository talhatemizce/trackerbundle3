import { useState, useEffect, useCallback, useRef, Component } from "react";

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

const BUILD_ID = "2026-02-25-v14-bf-dual-source";

const dollar = (v) => v != null ? `$${Math.round(v)}` : "—";
const fmtSecs = (s) => { if (!s || isNaN(s) || !isFinite(s)) return "default"; if (s >= 86400) return `${Math.round(s/86400)}d`; if (s >= 3600) return `${Math.round(s/3600)}h`; if (s >= 60) return `${Math.round(s/60)}m`; return `${s}s`; };
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
    await fetch("/telemetry/link-broken", {
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

function AlertsFeedTab({ C, push, isbns, titles, bookMeta = {} }) {
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

  const [bfData, setBfData] = useState({});  // { [isbn]: {loading, data, error} }
  const fetchBookfinder = async (isbn) => {
    setBfData(s => ({...s, [isbn]: {loading:true, data:null, error:null}}));
    try {
      const d = await req(`/bookfinder/${isbn}`, {}, 25000);
      setBfData(s => ({...s, [isbn]: {loading:false, data:d, error:null}}));
    } catch(e) {
      setBfData(s => ({...s, [isbn]: {loading:false, data:null, error:e.message}}));
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
      push(`${dedupIsbn} tekrar gönderilmek üzere işaretlendi — scheduler bir sonraki taramada alert atar`, "success");
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
          <option value="">Dedup temizle…</option>
          {isbns.map(i=><option key={i} value={i}>{i}</option>)}
        </select>
        <button
          onClick={clearDedup}
          disabled={!dedupIsbn}
          title="Seçili ISBN'in tekrar kaydını sil — scheduler bir sonraki taramada yeniden alert gönderir"
          style={{background:"none",border:`1px solid ${dedupIsbn?C.orange:C.border}`,borderRadius:5,color:dedupIsbn?C.orange:C.muted3,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:dedupIsbn?"pointer":"default",whiteSpace:"nowrap",transition:"all .15s"}}
        >
          🗑 Tekrarları temizle
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
            Test için: <b style={{color:C.accent}}>💉</b> butonu · Dedup dolu olabilir: dropdown'dan ISBN seç → <b style={{color:C.orange}}>🗑 Tekrarları temizle</b>
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
                background: isSelected ? (C===DARK?"#13131c":C.surface) : C.rowBg,
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
          onBfFetch={(isbn)=>fetchBookfinder(isbn)}
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
        width:440,maxWidth:"95vw",background:C.surface,
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
                    <div style={{background:"#1e3a5f22",border:"1px solid #3b82f633",borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:"#93c5fd",marginBottom:2,fontWeight:700}}>🛒 BB USED</div>
                      <div style={{fontSize:18,fontWeight:700,color:"#60a5fa"}}>${drawerData.amazon.used.buybox.total_int ?? drawerData.amazon.used.buybox.total}</div>
                      <div style={{fontSize:9,color:"#93c5fd",opacity:.7}}>{drawerData.amazon.used.buybox.label==="A"?"FBA":"FBM"} · Amazon</div>
                    </div>
                  )}
                  {drawerData.amazon?.available && drawerData.amazon?.new?.buybox && (
                    <div style={{background:"#14532d22",border:"1px solid #22c55e33",borderRadius:7,padding:"8px 10px"}}>
                      <div style={{fontSize:9,color:"#86efac",marginBottom:2,fontWeight:700}}>🛒 BB NEW</div>
                      <div style={{fontSize:18,fontWeight:700,color:"#22c55e"}}>${drawerData.amazon.new.buybox.total_int ?? drawerData.amazon.new.buybox.total}</div>
                      <div style={{fontSize:9,color:"#86efac",opacity:.7}}>{drawerData.amazon.new.buybox.label==="A"?"FBA":"FBM"} · Amazon</div>
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
                          {/* Combined */}
                          {ss.data.combined && (
                            <div style={{fontSize:10,color:C.muted3,textAlign:"center",padding:"4px 0"}}>
                              Toplam {ss.data.combined.count} satış · ort <b style={{color:C.text}}>${ss.data.combined.avg}</b>
                              {ss.data.cached && <span style={{marginLeft:6}}>⚡ cache</span>}
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
                      <div style={{marginBottom:10}}>
                        <div style={{fontSize:10,fontWeight:700,color:C.muted3,textTransform:"uppercase",letterSpacing:1,marginBottom:4}}>{label}</div>
                        {bb ? (
                          <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
                            <span style={{fontSize:18,fontWeight:800,color}}>${bb.total_int ?? bb.total}</span>
                            <span style={{fontSize:10,background:color+"22",color,padding:"2px 6px",borderRadius:4,fontWeight:700}}>
                              BuyBox · {fmtLabel(bb.label)}
                            </span>
                          </div>
                        ) : (
                          <div style={{fontSize:11,color:C.muted3,marginBottom:4}}>BuyBox yok</div>
                        )}
                        {top2.length > 0 && (
                          <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
                            {top2.map((o,i)=>(
                              <span key={i} style={{fontSize:10,background:C.surface2,padding:"2px 8px",borderRadius:4,color:C.muted}}>
                                #{i+1} ${o.total_int ?? o.total} {fmtLabel(o.label)}
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
                        ? "Amazon SP-API yapılandırılmamış"
                        : drawerData.amazon.note || drawerData.amazon.reason || "Veri yok"}
                    </div>
                  )}
                </AccordionSection>
              )}

              {/* ── Keepa Fiyat Geçmişi Grafiği ─────────────────────── */}
              <AccordionSection title="📈 Fiyat Geçmişi (Keepa)" C={C} defaultOpen={true}>
                {/* BuyBox anlık fiyat overlay — SP-API verisinden */}
                {drawerData.amazon?.available && (() => {
                  const az = drawerData.amazon;
                  const bbNew  = az.new?.buybox;
                  const bbUsed = az.used?.buybox;
                  const fmtLabel = l => l==="A"?"FBA":l==="M"?"FBM":l||"";
                  if (!bbNew && !bbUsed) return null;
                  return (
                    <div style={{display:"flex",gap:6,marginBottom:8,flexWrap:"wrap"}}>
                      {bbNew && (
                        <div style={{flex:1,minWidth:100,background:"#14532d22",border:"1px solid #22c55e44",borderRadius:6,padding:"6px 10px",display:"flex",alignItems:"center",gap:6}}>
                          <span style={{fontSize:9,color:"#86efac",fontWeight:700,textTransform:"uppercase",letterSpacing:.5}}>BB New</span>
                          <span style={{fontSize:16,fontWeight:800,color:"#22c55e"}}>${bbNew.total_int ?? bbNew.total}</span>
                          <span style={{fontSize:9,color:"#86efac",opacity:.7}}>{fmtLabel(bbNew.label)}</span>
                        </div>
                      )}
                      {bbUsed && (
                        <div style={{flex:1,minWidth:100,background:"#1e3a5f22",border:"1px solid #3b82f644",borderRadius:6,padding:"6px 10px",display:"flex",alignItems:"center",gap:6}}>
                          <span style={{fontSize:9,color:"#93c5fd",fontWeight:700,textTransform:"uppercase",letterSpacing:.5}}>BB Used</span>
                          <span style={{fontSize:16,fontWeight:800,color:"#60a5fa"}}>${bbUsed.total_int ?? bbUsed.total}</span>
                          <span style={{fontSize:9,color:"#93c5fd",opacity:.7}}>{fmtLabel(bbUsed.label)}</span>
                        </div>
                      )}
                      <div style={{width:"100%",fontSize:9,color:C.muted3,marginTop:-4}}>⚡ SP-API anlık · Keepa grafiğinde BuyBox çizgisi Pro gerektirir</div>
                    </div>
                  );
                })()}
                <div style={{position:"relative",background:C.surface2,borderRadius:8,overflow:"hidden",marginBottom:8}}>
                  {/* Loading placeholder */}
                  <div id={`keepa-loading-${isbn}`} style={{textAlign:"center",padding:"20px 0",fontSize:11,color:C.muted3}}>
                    ⏳ Keepa grafiği yükleniyor…
                  </div>
                  <a href={`https://keepa.com/#!search/1-${isbn}`} target="_blank" rel="noreferrer"
                    style={{display:"block"}}>
                    <img
                      src={`https://graph.keepa.com/pricehistory.png?asin=${isbn}&domain=com&range=180&new=1&used=1&salesrank=1&width=500&height=200`}
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
                        if(loader)loader.innerHTML='<span style="color:#f59e0b">Keepa grafiği yüklenemedi</span> · <a href="https://keepa.com/#!search/1-'+isbn+'" target="_blank" rel="noreferrer" style="color:#3b82f6">Keepa\'da aç ↗</a>';
                      }}
                    />
                  </a>
                </div>
                <div style={{display:"flex",gap:8,justifyContent:"center",flexWrap:"wrap",marginTop:4}}>
                  <a href={`https://keepa.com/#!search/1-${isbn}`} target="_blank" rel="noreferrer"
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

              {/* ── BookFinder Fiyat Karşılaştırma ─────────────────────── */}
              <AccordionSection title="📚 Fiyat Karşılaştır (BookFinder)" C={C} defaultOpen={true}>
                {(()=>{
                  const bf = bfScrape;
                  const canFetch = !bf?.loading;
                  const showBtn = canFetch && !bf?.data;
                  const doFetch = () => { if(onBfFetch){onBfFetch(isbn);} else {console.error("onBfFetch prop missing!");} };
                  return (
                    <div>
                      {showBtn && (
                        <button
                          onClick={doFetch}
                          style={{width:"100%",padding:"10px",borderRadius:6,fontSize:12,fontWeight:700,
                            background:C.purple||"#7c3aed",color:"white",
                            cursor:"pointer",display:"flex",alignItems:"center",justifyContent:"center",gap:6,
                            border:"none",boxShadow:"0 2px 8px rgba(124,58,237,.3)"}}
                        >
                          📚 BookFinder Fiyatlarını Getir
                        </button>
                      )}
                      {!showBtn && !bf?.data && !bf?.loading && (
                        <div style={{fontSize:10,color:C.muted3,textAlign:"center",padding:4}}>BookFinder bağlantısı hazır değil</div>
                      )}
                      {bf?.loading && (
                        <div style={{textAlign:"center",padding:"12px 0"}}>
                          <div style={{fontSize:11,color:C.purple||"#7c3aed",fontWeight:600}}>⏳ BookFinder fiyatları çekiliyor…</div>
                          <div style={{fontSize:9,color:C.muted3,marginTop:4}}>AbeBooks · Alibris · Biblio · BetterWorldBooks</div>
                        </div>
                      )}
                      {bf?.error && (
                        <div style={{background:C.surface2,borderRadius:6,padding:"10px 12px",marginBottom:8}}>
                          <div style={{fontSize:11,color:C.orange,marginBottom:6}}>⚠ {bf.error}</div>
                          <button onClick={doFetch} style={{fontSize:10,padding:"4px 12px",borderRadius:4,background:"none",border:`1px solid ${C.accent}`,color:C.accent,cursor:"pointer"}}>↺ Tekrar Dene</button>
                        </div>
                      )}
                      {bf?.data && !bf.data.ok && (
                        <div style={{background:C.surface2,borderRadius:6,padding:"10px 12px",marginBottom:8}}>
                          <div style={{fontSize:11,color:C.orange,marginBottom:6}}>⚠ {bf.data.error || "BookFinder verisi alınamadı"}</div>
                          <button onClick={doFetch} style={{fontSize:10,padding:"4px 12px",borderRadius:4,background:"none",border:`1px solid ${C.accent}`,color:C.accent,cursor:"pointer"}}>↺ Tekrar Dene</button>
                        </div>
                      )}
                      {bf?.data?.ok && (
                        <div>
                          {/* Summary: New + Used side by side */}
                          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginBottom:10}}>
                            {[["🆕 New",bf.data.new,C.green],["🧺 Used",bf.data.used,C.accent]].map(([label,st,col])=>(
                              <div key={label} style={{background:C.surface2,borderRadius:6,padding:"8px 10px"}}>
                                <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:6}}>
                                  <span style={{fontSize:10,color:col,fontWeight:700}}>{label}</span>
                                  {st
                                    ? <span style={{fontSize:9,color:C.muted3}}>{st.count} ilan</span>
                                    : <span style={{fontSize:9,color:C.muted3}}>yok</span>}
                                </div>
                                {st ? (
                                  <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:3}}>
                                    {[["Min",st.min],["Avg",st.avg],["Max",st.max],["#",st.count]].map(([k,v])=>(
                                      <div key={k} style={{textAlign:"center"}}>
                                        <div style={{fontSize:7,color:C.muted3}}>{k}</div>
                                        <div style={{fontSize:12,fontWeight:700,color:k==="Min"?col:C.text,fontFamily:"var(--mono)"}}>{k==="#"?v:`$${v}`}</div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <div style={{fontSize:10,color:C.muted3,textAlign:"center"}}>—</div>
                                )}
                              </div>
                            ))}
                          </div>
                          {/* Average across all offers */}
                          {bf.data.all_avg != null && (
                            <div style={{fontSize:10,color:C.muted3,textAlign:"center",padding:"4px 0",marginBottom:8}}>
                              Toplam {bf.data.total_offers} ilan · genel ort <b style={{color:C.text}}>${bf.data.all_avg}</b>
                              {bf.data.cached && <span style={{marginLeft:6}}>⚡ cache</span>}
                            </div>
                          )}
                          {/* Per-seller breakdown table */}
                          {(bf.data.used?.offers || bf.data.new?.offers) && (
                            <div style={{marginTop:4}}>
                              <div style={{fontSize:9,color:C.muted,fontWeight:600,marginBottom:4,letterSpacing:"0.04em"}}>EN UCUZ İLANLAR</div>
                              <table style={{width:"100%",borderCollapse:"collapse",fontSize:10}}>
                                <thead>
                                  <tr style={{borderBottom:`1px solid ${C.border}`}}>
                                    {["Satıcı","Durum","Fiyat","Kargo","Toplam"].map(h=>(
                                      <th key={h} style={{textAlign:h==="Satıcı"||h==="Durum"?"left":"right",padding:"3px 4px",fontSize:8,color:C.muted,fontWeight:500}}>{h}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {[...(bf.data.used?.offers||[]).slice(0,6),...(bf.data.new?.offers||[]).slice(0,4)]
                                    .sort((a,b)=>a.total-b.total)
                                    .slice(0,10)
                                    .map((o,i)=>(
                                      <tr key={i} style={{borderBottom:`1px solid ${C.border}10`}}>
                                        <td style={{padding:"4px",color:C.text,fontWeight:500,maxWidth:80,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{o.seller}</td>
                                        <td style={{padding:"4px",color:o.condition==="NEW"?C.green:C.accent,fontSize:9}}>{o.condition==="NEW"?"New":"Used"}</td>
                                        <td style={{padding:"4px",textAlign:"right",color:C.muted,fontFamily:"var(--mono)"}}>${o.price}</td>
                                        <td style={{padding:"4px",textAlign:"right",color:C.muted3,fontFamily:"var(--mono)"}}>{o.shipping>0?`$${o.shipping}`:"free"}</td>
                                        <td style={{padding:"4px",textAlign:"right",color:C.text,fontWeight:700,fontFamily:"var(--mono)"}}>${o.total}</td>
                                      </tr>
                                    ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                          {/* BookFinder link + refresh */}
                          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginTop:8}}>
                            <a href={bf.data.bookfinder_url} target="_blank" rel="noreferrer"
                              style={{fontSize:9,color:C.muted3,textDecoration:"none"}}>BookFinder ↗</a>
                            <button onClick={()=>onBfFetch && onBfFetch(isbn)} style={{fontSize:9,background:"none",border:"none",color:C.muted3,cursor:"pointer"}}>↺ Yenile</button>
                          </div>
                        </div>
                      )}
                      {bf?.data && !bf.data.ok && (
                        <div style={{fontSize:11,color:C.orange,textAlign:"center",padding:"6px 0"}}>
                          ⚠ {bf.data.error || "Veri alınamadı"}
                          <button onClick={()=>onBfFetch && onBfFetch(isbn)} style={{marginLeft:8,fontSize:10,background:"none",border:"none",color:C.accent,cursor:"pointer"}}>↺</button>
                        </div>
                      )}
                      {bf?.data?.ok && !bf.data.new && !bf.data.used && (
                        <div style={{fontSize:11,color:C.muted3,textAlign:"center",padding:"6px 0"}}>Bu ISBN için ilan bulunamadı.</div>
                      )}
                    </div>
                  );
                })()}
              </AccordionSection>

              {/* ── Score Analizi — only for alert entries ────────────────── */}
              {alertEntry?.deal_score != null && (
                <AccordionSection title={`🧮 Score Analizi · ${alertEntry.deal_score}/100`} C={C} defaultOpen={false}>
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
        <div style={{padding:"12px 18px",borderTop:`1px solid ${C.border}`,display:"flex",gap:10,flexShrink:0}}>
          <a href={buildEbaySearchUrl({isbn})} target="_blank" rel="noreferrer"
            style={{flex:1,padding:"9px",borderRadius:7,background:"#e53238",color:"white",
              textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:12,fontFamily:"var(--mono)"}}>
            🛍 eBay
          </a>
          <a href={`https://www.bookfinder.com/isbn/${isbn}/`} target="_blank" rel="noreferrer"
            style={{flex:1,padding:"9px",borderRadius:7,background:"#6366f1",color:"white",
              textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:12,fontFamily:"var(--mono)"}}>
            📚 BookFinder
          </a>
          {alertEntry?.url && (
            <a href={alertEntry.url} target="_blank" rel="noreferrer"
              style={{flex:1,padding:"9px",borderRadius:7,background:"#FF9900",color:"white",
                textDecoration:"none",textAlign:"center",fontWeight:700,fontSize:12,fontFamily:"var(--mono)"}}>
              🛒 İlan ↗
            </a>
          )}
          <button onClick={onClose}
            style={{flex:alertEntry?.url?0:1,padding:"9px 16px",borderRadius:7,background:"none",
              border:`1px solid ${C.border}`,color:C.muted,cursor:"pointer",fontFamily:"var(--mono)",fontSize:12}}>
            Kapat
          </button>
        </div>
      </div>
    </>
  );
}

const TABS = ["dashboard","watchlist","pricing","alerts"];

export default function App() {
  return <ErrorBoundary><AppReal /></ErrorBoundary>;
}
function AppReal() {
  const [isDark, setIsDark] = useState(() => {
    try { return localStorage.getItem("tb_theme") !== "light"; } catch { return true; }
  });
  const C = isDark ? DARK : LIGHT;
  useEffect(() => {
    try { localStorage.setItem("tb_theme", isDark ? "dark" : "light"); } catch {}
  }, [isDark]);
  const [tab, setTab] = useState("dashboard");
  const { toasts, push } = useToast();

  const [isbns, setIsbns] = useState([]);
  const [intervals, setIntervals] = useState({});
  const [status, setStatus] = useState(null);
  const [alertStats, setAlertStats] = useState({});
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
  const fetchWlBookfinder = async (isbn) => {
    setWlBfData(s=>({...s,[isbn]:{loading:true,data:null,error:null}}));
    try {
      const d = await req(`/bookfinder/${isbn}`, {}, 25000);
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
      const [a,b,c,d,e] = await Promise.allSettled([req("/isbns"),req("/rules"),req("/status"),req("/alerts/stats"),req("/run-state")]);
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
    try { await req(`/alerts/${isbn}`,{method:"DELETE"}); setAlertStats(p=>{const r={...p};delete r[isbn];return r;}); push("Alertler temizlendi","success"); }
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
            <span style={{fontSize:9,color:C.muted3,letterSpacing:"0.05em",background:C.surface2,border:`1px solid ${C.border}`,borderRadius:3,padding:"1px 5px",fontFamily:"var(--mono)"}}>{BUILD_ID}</span>
          </div>
          <div style={{display:"flex",alignItems:"center",gap:12}}>
            {status?.ok
              ? <><span style={{width:7,height:7,borderRadius:"50%",background:C.green,boxShadow:`0 0 6px ${C.green}`,display:"inline-block"}}/><span style={{fontSize:11,color:C.green,letterSpacing:"0.05em"}}>LIVE</span></>
              : <><span style={{width:7,height:7,borderRadius:"50%",background:C.red,display:"inline-block"}}/><span style={{fontSize:11,color:C.red}}>OFFLINE</span></>}
            {status&&<span style={{fontSize:11,color:C.muted3}}>{new Date(status.time_utc).toLocaleTimeString("tr-TR")}</span>}
            <button className="icon-btn" onClick={load} title="Yenile">↻</button>
            <button onClick={()=>setIsDark(d=>!d)} style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:6,cursor:"pointer",padding:"5px 10px",fontSize:15,color:C.text,transition:"all .2s"}} title={isDark?"Açık tema":"Koyu tema"}>
              {isDark?"☀️":"🌙"}
            </button>
          </div>
        </div>
        <div style={{display:"flex"}}>
          {TABS.map(t=>{
            const label = {dashboard:"Dashboard",watchlist:"Watchlist",pricing:"Pricing",alerts:"Alerts"}[t]||t;
            return <button key={t} className="tab-btn" onClick={()=>setTab(t)} style={{padding:"10px 20px",fontSize:12,color:tab===t?C.accent:C.muted,borderBottom:tab===t?`2px solid ${C.accent}`:"2px solid transparent",fontWeight:tab===t?600:400,letterSpacing:"0.01em"}}>{label}</button>;
          })}
        </div>
      </div>

      {/* Content */}
      <div style={{padding:"28px 32px",maxWidth:1100,margin:"0 auto"}}>
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
                {Object.keys(alertStats).length>0&&(
                  <div style={{marginBottom:24}}>
                    <ST C={C}>Bildirim Gönderilen ISBNler</ST>
                    {Object.entries(alertStats).map(([isbn,count])=>(
                      <div key={isbn} className="row-item" style={{...row}}>
                        <span style={{flex:1,fontSize:13}}>{isbn}</span>
                        <span className="badge" style={{background:isDark?"#1a2a1a":"#f0fdf4",color:C.green}}>🎯 {count}</span>
                        <button className="icon-btn" style={{color:C.red,fontSize:12}} onClick={()=>clearAlerts(isbn)}>Temizle</button>
                      </div>
                    ))}
                  </div>
                )}
                <ST C={C}>Watchlist Önizleme</ST>
                {isbns.slice(0,5).map(isbn=>(
                  <div key={isbn} className="row-item" style={{...row}}>
                    <div style={{flex:1,display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                      <span style={{fontSize:13}}>{isbn}</span>
                      {titles[isbn]&&<span style={{fontSize:11,color:C.muted,fontFamily:"var(--sans)"}}>— {titles[isbn]}</span>}
                    </div>
                    <span style={{fontSize:10,color:C.muted}}>interval: {fmtSecs(intervals[isbn])}</span>
                    {runState[isbn]&&<span style={{fontSize:10,color:C.muted2}}>son: {fmtTime(runState[isbn])}</span>}
                    {alertStats[isbn]>0&&<span className="badge" style={{background:isDark?"#1a2a1a":"#f0fdf4",color:C.green,fontSize:10}}>🎯 {alertStats[isbn]}</span>}
                    <span style={{width:8,height:8,borderRadius:"50%",background:C.green,display:"inline-block"}}/>
                  </div>
                ))}
                {isbns.length>5&&<div style={{fontSize:11,color:C.muted3,marginTop:8}}>+{isbns.length-5} daha</div>}
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
                        style={{borderBottom:editingRule===isbn?`1px solid ${C.border}`:"none",marginBottom:0,paddingBottom:editingRule===isbn?12:undefined,cursor:(editingRule||editing)?"default":"pointer",transition:"background .12s"}}
                      >
                        <div style={{flex:1}}>
                          <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                            <span style={{fontFamily:"var(--sans)",fontSize:13,fontWeight:600}}>{isbn}</span>
                            {titles[isbn]&&<span style={{fontSize:12,color:C.muted,fontFamily:"var(--sans)"}}>— {titles[isbn]}</span>}
                            {titles[isbn]===null&&<span style={{fontSize:10,color:C.muted3}}>…</span>}
                            {bookMeta[isbn]?.author&&<span style={{fontSize:10,color:C.muted3,fontFamily:"var(--sans)"}}>{bookMeta[isbn].author}{bookMeta[isbn].year?` · ${bookMeta[isbn].year}`:""}</span>}
                            {alertStats[isbn]>0&&<span className="badge" style={{background:isDark?"#1a2a1a":"#f0fdf4",color:C.green}}>🎯 {alertStats[isbn]}</span>}
                          </div>
                          <div style={{fontSize:10,color:C.muted2,marginTop:3,display:"flex",gap:12}}>
                            <span>{runState[isbn]?`son tarama: ${fmtTime(runState[isbn])}`:"henüz taranmadı"}</span>
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
                          <button onClick={()=>{setEditing(isbn);setEditVal(fmtSecs(intervals[isbn])==="default"?"":fmtSecs(intervals[isbn]));}} style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:4,color:intervals[isbn]?C.blue:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"3px 10px",cursor:"pointer"}}>
                            ⏱ {fmtSecs(intervals[isbn])}
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

{tab==="alerts"&&<AlertsFeedTab C={C} push={push} isbns={isbns} titles={titles} bookMeta={bookMeta}/>}
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
