import { useState, useEffect, useCallback } from "react";

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

const BUILD_ID = "2026-02-24-proxy-fix";

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
    LH_BIN: "1",
    rt:     "nc",
    _sop:   sort === "cheapest" ? "15" : "12",   // 15=price+ship asc, 12=best match
  });
  const cid = condition && _EBAY_COND_IDS[condition];
  if (cid) params.set("LH_ItemCondition", cid);
  return `https://www.ebay.com/sch/i.html?${params.toString()}`;
}

// Unit-like smoke tests (run once at module load, log any mismatch)
;(() => {
  const cases = [
    { input: { isbn: "9780132350884" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780132350884&_sacat=267&LH_BIN=1&rt=nc&_sop=15" },
    { input: { isbn: "9780132350884", condition: "good" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780132350884&_sacat=267&LH_BIN=1&rt=nc&_sop=15&LH_ItemCondition=5000" },
    { input: { isbn: "9780974769431", condition: "like_new", sort: "cheapest" },
      expect: "https://www.ebay.com/sch/i.html?_nkw=9780974769431&_sacat=267&LH_BIN=1&rt=nc&_sop=15&LH_ItemCondition=3000" },
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
  return <div style={{fontSize:11,letterSpacing:"0.1em",textTransform:"uppercase",color:C.muted,marginBottom:12,...style}}>{children}</div>;
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
      {/* Proxy banner */}
      {isProxy && (
        <div style={{background:"rgba(251,146,60,.1)",border:`1px solid ${C.orange}`,borderRadius:6,padding:"6px 10px",marginBottom:12,fontSize:10,color:C.orange,lineHeight:1.5}}>
          📊 <b>Proxy veri</b> — Satış verisi yok (Finding API kota aşımı). Aktif eBay listeleme fiyatlarından hesaplandı.
          Gerçek satış fiyatından sapma olabilir.
        </div>
      )}
      {/* Başlık + Önerilen fiyat */}
      <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:16}}>
        <div>
          <div style={{fontSize:11,color:C.muted,marginBottom:4,letterSpacing:"0.06em",textTransform:"uppercase"}}>{label}</div>
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

  useEffect(() => {
    req("/ebay/debug/finding-backoff", {}, 5000).then(setBackoff).catch(() => setBackoff(null));
  }, []);

  const clearBackoff = async () => {
    try {
      await fetch(BASE + "/ebay/debug/finding-backoff", {method:"DELETE"});
      const b = await req("/ebay/debug/finding-backoff", {}, 5000);
      setBackoff(b);
      push("Backoff temizlendi — bir sonraki hesaplamada Finding API yeniden denenir", "success");
    } catch(e) { push("Temizlenemedi: " + e.message, "error"); }
  };

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
      {/* Finding API Backoff Banner */}
      {backoff?.active && (
        <div style={{background:"rgba(248,113,113,.08)",border:`1px solid ${C.red}`,borderRadius:8,padding:"12px 16px",marginBottom:16,display:"flex",alignItems:"center",gap:12}}>
          <span style={{fontSize:18}}>⏸</span>
          <div style={{flex:1}}>
            <div style={{color:C.red,fontSize:12,fontWeight:600}}>Finding API Backoff Aktif — Sold Stats Devre Dışı</div>
            <div style={{color:C.muted,fontSize:11,marginTop:2,lineHeight:1.7}}>
              {backoff.backoff_until_epoch
                ? <>Bitiş: <b style={{color:C.orange}}>
                    {new Date(backoff.backoff_until_epoch*1000).toLocaleString("tr-TR")}
                  </b> · kalan: <b style={{color:C.orange}}>{
                    backoff.remaining_seconds >= 3600
                      ? `${Math.round(backoff.remaining_seconds/3600)} saat`
                      : `${Math.round(backoff.remaining_seconds/60)} dk`
                  }</b></>
                : "Süre bilinmiyor"
              }<br/>
              <span style={{fontSize:10,color:C.muted3}}>
                Fiyat tahmini aktif listelerden hesaplanıyor (Browse proxy) — "sold avg" değil.
                Temizlemek eBay kotasını sıfırlamaz; sadece yerel kilidi kaldırır.
              </span>
            </div>
          </div>
          <button onClick={clearBackoff} style={{background:"none",border:`1px solid ${C.red}`,borderRadius:5,color:C.red,fontFamily:"var(--mono)",fontSize:11,padding:"5px 12px",cursor:"pointer",whiteSpace:"nowrap"}}>
            🔓 Kilidi Kaldır
          </button>
        </div>
      )}

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
                <div style={{fontSize:10,color:C.muted,marginBottom:8,letterSpacing:"0.08em",textTransform:"uppercase"}}>Kondisyon Kırılımı</div>
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
                <div style={{fontSize:10,color:C.muted,marginBottom:8,letterSpacing:"0.08em",textTransform:"uppercase"}}>En Ucuz 10 İlan</div>
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
function ScoreBadge({ score, C }) {
  if (score == null) return null;
  const color  = score >= 75 ? C.green : score >= 50 ? C.accent : C.muted;
  const label  = score >= 75 ? "🔥" : score >= 50 ? "✨" : "";
  const border = `1px solid ${color}`;
  return (
    <span title={`Deal Score: ${score}/100`} style={{
      fontSize:11, fontWeight:700, color,
      background:"rgba(255,255,255,.04)", border, borderRadius:4,
      padding:"1px 7px", letterSpacing:"0.02em", flexShrink:0,
    }}>
      {label}{score}
    </span>
  );
}

// Thumbnail with eBay → OpenLibrary fallback, safe against infinite onError loops
function Thumb({ imageUrl, isbn, href, C }) {
  const olCover = `https://covers.openlibrary.org/b/isbn/${isbn}-S.jpg`;
  const [src, setSrc] = useState(imageUrl || olCover);
  const [triedOl, setTriedOl] = useState(!imageUrl); // if no eBay url, already on OL
  return (
    <a href={href || "#"} target="_blank" rel="noreferrer" style={{flexShrink:0}}>
      <img
        src={src}
        loading="lazy"
        width={56} height={56}
        style={{borderRadius:6,objectFit:"cover",background:C.surface2,border:`1px solid ${C.border}`,display:"block"}}
        onError={() => {
          if (!triedOl) {
            setTriedOl(true);
            setSrc(olCover);
          }
          // if OL also fails, leave broken (img hides itself gracefully with background)
        }}
        alt=""
      />
    </a>
  );
}

function AlertsFeedTab({ C, push, isbns, titles }) {
  const [entries, setEntries] = useState([]);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [isbnFilter, setIsbnFilter] = useState("");
  const [condFilter, setCondFilter] = useState("");      // "" | brand_new | like_new | ...
  const [decisionFilter, setDecisionFilter] = useState(""); // "" | BUY | OFFER
  const [sortBy, setSortBy] = useState("ts");            // "ts" | "score" | "total"
  const [dedupIsbn, setDedupIsbn] = useState("");   // React state — no getElementById

  const load = async () => {
    setLoading(true);
    try {
      const url = isbnFilter ? `/alerts/history?limit=100&isbn=${isbnFilter}` : "/alerts/history?limit=100";
      const [h, s] = await Promise.allSettled([req(url), req("/alerts/summary")]);
      if (h.status === "fulfilled") setEntries(h.value.entries || []);
      if (s.status === "fulfilled") setSummary(s.value);
    } catch(e) { push("Yüklenemedi: "+e.message, "error"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, [isbnFilter]);

  const clearDedup = async () => {
    if (!dedupIsbn) { push("ISBN seç", "error"); return; }
    try {
      await req(`/alerts/dedup/${dedupIsbn}`, {method:"DELETE"});
      push(`${dedupIsbn} dedup temizlendi — bir sonraki scheduler tick'inde tekrar alert gönderilir`, "success");
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

        <div style={{width:1,height:24,background:C.border,flexShrink:0}}/>

        {/* Dedup clear — proper React state */}
        <select className="inp" value={dedupIsbn} onChange={e=>setDedupIsbn(e.target.value)} style={{flex:"1 1 180px",minWidth:150,background:C.inputBg,border:`1px solid ${C.inputBorder}`,color:C.text,fontSize:12}}>
          <option value="">Dedup temizle…</option>
          {isbns.map(i=><option key={i} value={i}>{i}</option>)}
        </select>
        <button
          onClick={clearDedup}
          disabled={!dedupIsbn}
          title="Seçili ISBN'in dedup kaydını sil — scheduler bir sonraki tick'te tekrar alert gönderebilir"
          style={{background:"none",border:`1px solid ${dedupIsbn?C.orange:C.border}`,borderRadius:5,color:dedupIsbn?C.orange:C.muted3,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:dedupIsbn?"pointer":"default",whiteSpace:"nowrap",transition:"all .15s"}}
        >
          🗑 Dedup Sil
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
            Test için: <b style={{color:C.accent}}>💉</b> butonu · Dedup dolu olabilir: dropdown'dan ISBN seç → <b style={{color:C.orange}}>🗑 Dedup Sil</b>
          </span>
        </div>
      )}

      {entries
        .filter(e => !condFilter     || e.condition === condFilter)
        .filter(e => !decisionFilter || e.decision  === decisionFilter)
        .slice()
        .sort((a, b) => {
          if (sortBy === "score")  return (b.deal_score ?? -1) - (a.deal_score ?? -1);
          if (sortBy === "total")  return a.total - b.total;
          return b.ts - a.ts; // default: newest first
        })
        .map((e, i) => {
        return (
          <div key={`${e.item_id}-${i}`} style={{background:C.rowBg,border:`1px solid ${C.rowBorder}`,borderLeft:`3px solid ${condColor(e.condition,C)}`,borderRadius:8,padding:"12px 14px",marginBottom:8,display:"flex",gap:12,alignItems:"flex-start"}}>
            {/* Thumbnail — eBay imageUrl → OL cover fallback (safe, no loop) */}
            <Thumb imageUrl={e.image_url} isbn={e.isbn} href={e.url} C={C} />
            {/* Content */}
            <div style={{flex:1,minWidth:0}}>
              <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap",marginBottom:3}}>
                <span style={{fontSize:12,fontWeight:600,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",maxWidth:280}}>{e.title||e.isbn}</span>
                <span style={{fontSize:10,color:condColor(e.condition,C),background:"rgba(255,255,255,.04)",border:`1px solid ${condColor(e.condition,C)}`,borderRadius:3,padding:"1px 6px"}}>{condLabel[e.condition]||e.condition}</span>
                <span style={{fontSize:11,fontWeight:600,color:e.decision==="BUY"?C.green:C.blue}}>
                  {e.decision==="BUY"?"🟢 BUY":"🟡 OFFER"}
                </span>
                <ScoreBadge score={e.deal_score} C={C} />
                {e.match_quality==="CONFIRMED"
                  ? <span style={{fontSize:10,color:C.green,background:"rgba(52,211,153,.1)",border:`1px solid ${C.green}`,borderRadius:3,padding:"1px 6px"}}>✅ confirmed</span>
                  : e.match_quality==="UNVERIFIED_SUPER_DEAL"
                    ? <span style={{fontSize:10,color:C.orange,background:"rgba(251,146,60,.1)",border:`1px solid ${C.orange}`,borderRadius:3,padding:"1px 6px"}}>⚠ super deal</span>
                    : null
                }
              </div>
              <div style={{fontSize:11,color:C.muted,display:"flex",gap:12,flexWrap:"wrap"}}>
                <span>ISBN: {e.isbn}{titles[e.isbn]?` · ${titles[e.isbn]}`:""}</span>
                <span style={{color:C.text,fontWeight:600}}>${e.total}</span>
                <span style={{color:C.muted3}}>limit: ${e.limit}</span>
                {e.sold_avg && <span>sold avg: ${e.sold_avg}</span>}
                {e.ship_estimated && <span style={{color:C.orange}}>⚠ est.ship</span>}
                {e.verification_reason && e.verification_reason!=="gtins_match" && (
                  <span style={{fontSize:10,color:C.muted3}}>{e.verification_reason}</span>
                )}
              </div>
            </div>
            {/* Time + link */}
            <div style={{flexShrink:0,textAlign:"right"}}>
              <div style={{fontSize:10,color:C.muted3,marginBottom:6}}>{fmtTs(e.ts)}</div>
              {e.url && (
                <a href={e.url} target="_blank" rel="noreferrer" style={{fontSize:11,color:C.accent,textDecoration:"none",border:`1px solid ${C.accent}`,borderRadius:4,padding:"2px 8px",whiteSpace:"nowrap"}}>
                  eBay →
                </a>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

const TABS = ["dashboard","watchlist","pricing","alerts"];

export default function App() {
  const [isDark, setIsDark] = useState(true);
  const C = isDark ? DARK : LIGHT;
  const [tab, setTab] = useState("dashboard");
  const { toasts, push } = useToast();

  const [isbns, setIsbns] = useState([]);
  const [intervals, setIntervals] = useState({});
  const [status, setStatus] = useState(null);
  const [alertStats, setAlertStats] = useState({});
  const [runState, setRunState] = useState({});
  const [loading, setLoading] = useState(true);
  const [backoffStatus, setBackoffStatus] = useState(null);

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
      const [a,b,c,d,e,f] = await Promise.allSettled([req("/isbns"),req("/rules"),req("/status"),req("/alerts/stats"),req("/run-state"),req("/ebay/debug/finding-backoff",{},5000)]);
      if (a.status==="fulfilled") setIsbns(a.value.items||[]);
      if (b.status==="fulfilled") {
        setIntervals(b.value.intervals||{});
        setRules(b.value.rules||{});
      }
      if (c.status==="fulfilled") setStatus(c.value);
      if (d.status==="fulfilled") setAlertStats(d.value.stats||{});
      if (e.status==="fulfilled") setRunState(e.value.by_isbn||{});
      if (f.status==="fulfilled") setBackoffStatus(f.value);
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
          {TABS.map(t=>(
            <button key={t} className="tab-btn" onClick={()=>setTab(t)} style={{padding:"10px 20px",fontSize:11,letterSpacing:"0.08em",textTransform:"uppercase",color:tab===t?C.accent:C.muted,borderBottom:tab===t?`2px solid ${C.accent}`:"2px solid transparent",fontWeight:tab===t?600:400}}>{t}</button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{padding:"28px 32px",maxWidth:1100,margin:"0 auto"}}>
        {loading ? <div style={{color:C.muted3,textAlign:"center",paddingTop:80,fontSize:13}}>Yükleniyor…</div> : (
          <>
            {tab==="dashboard"&&(
              <div>
                <div style={{marginBottom:10,color:C.muted3,fontSize:11,letterSpacing:"0.1em",textTransform:"uppercase"}}>
                  Overview · {new Date().toLocaleDateString("tr-TR",{day:"numeric",month:"long",year:"numeric"})}
                </div>
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:16}}>
                  <StatCard C={C} icon="📚" label="Toplam ISBN" value={isbns.length} sub="watchlist'te" accent={C.accent}/>
                  <StatCard C={C} icon="🔄" label="Tarama Yapıldı" value={Object.keys(runState).length} sub="run_state kayıtları" accent={C.blue}/>
                  <StatCard C={C} icon="🎯" label="Toplam Alert" value={totalAlerts} sub="benzersiz item" accent={C.green}/>
                  <StatCard C={C} icon="🔔" label="Bot Token" value={status?.has_bot_token?"✓":"✗"} sub={status?.has_bot_token?"Telegram aktif":"Token yok"} accent={status?.has_bot_token?C.green:C.red}/>
                </div>

                {/* Finding API Backoff Banner — dashboard */}
                {backoffStatus?.active && (
                  <div style={{background:"rgba(248,113,113,.07)",border:`1px solid ${C.red}`,borderRadius:10,padding:"14px 18px",marginBottom:20,display:"flex",alignItems:"center",gap:14}}>
                    <span style={{fontSize:20}}>⏸</span>
                    <div style={{flex:1}}>
                      <div style={{color:C.red,fontSize:12,fontWeight:600,marginBottom:3}}>Finding API Backoff — Sold Stats Devre Dışı</div>
                      <div style={{color:C.muted,fontSize:11}}>
                        Bitiş: <b style={{color:C.orange}}>
                          {new Date(backoffStatus.backoff_until_epoch*1000).toLocaleString("tr-TR")}
                        </b>
                        {" · "}kalan: <b style={{color:C.orange}}>{
                          backoffStatus.remaining_seconds >= 3600
                            ? `${Math.round(backoffStatus.remaining_seconds/3600)} saat`
                            : `${Math.round(backoffStatus.remaining_seconds/60)} dk`
                        }</b>
                        <span style={{color:C.muted3,fontSize:10,marginLeft:8}}>· Fiyat tahmini Browse proxy üzerinden çalışıyor</span>
                      </div>
                    </div>
                    <button onClick={async()=>{
                      try {
                        await fetch(BASE+"/ebay/debug/finding-backoff",{method:"DELETE"});
                        setBackoffStatus({active:false,remaining_seconds:0,backoff_until_epoch:0});
                        push("Backoff kilidi kaldırıldı","success");
                      } catch(e){push(e.message,"error");}
                    }} style={{background:"none",border:`1px solid ${C.red}`,borderRadius:5,color:C.red,fontFamily:"var(--mono)",fontSize:11,padding:"6px 14px",cursor:"pointer",whiteSpace:"nowrap"}}>
                      🔓 Kilidi Kaldır
                    </button>
                  </div>
                )}
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
                      <div className="row-item" style={{borderBottom:editingRule===isbn?`1px solid ${C.border}`:"none",marginBottom:0,paddingBottom:editingRule===isbn?12:undefined}}>
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

{tab==="alerts"&&<AlertsFeedTab C={C} push={push} isbns={isbns} titles={titles}/>}
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
