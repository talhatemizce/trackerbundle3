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

const BUILD_ID = "2026-02-22-browse-proxy";

const dollar = (v) => v != null ? `$${Math.round(v)}` : "—";
const fmtSecs = (s) => { if (!s) return "default"; if (s >= 86400) return `${Math.round(s/86400)}d`; if (s >= 3600) return `${Math.round(s/3600)}h`; return `${Math.round(s/60)}m`; };
const parseSecs = (str) => { const m = String(str).trim().match(/^(\d+(?:\.\d+)?)(d|h|m|s)?$/i); if (!m) return null; const n = parseFloat(m[1]), u = (m[2]||"h").toLowerCase(); return Math.round(u==="d"?n*86400:u==="h"?n*3600:u==="m"?n*60:n); };
const fmtTime = (unix) => unix ? new Date(unix*1000).toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit"}) : "—";

function useToast() {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((msg, type="info") => { const id=Date.now()+Math.random(); setToasts(t=>[...t,{id,msg,type}]); setTimeout(()=>setToasts(t=>t.filter(x=>x.id!==id)),3200); }, []);
  return { toasts, push };
}

// OpenLibrary title cache: localStorage + per-ISBN fetch
function useBookTitles(isbns) {
  const [titles, setTitles] = useState(() => {
    try { return JSON.parse(localStorage.getItem("ol_titles") || "{}"); } catch { return {}; }
  });
  const isbnKey = isbns.join(",");
  useEffect(() => {
    const missing = isbns.filter(isbn => titles[isbn] === undefined);
    if (!missing.length) return;
    setTitles(t => { const n = {...t}; missing.forEach(i => { n[i] = null; }); return n; });
    missing.forEach(isbn => {
      fetch(`https://openlibrary.org/isbn/${isbn}.json`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          const title = d?.title || "";
          setTitles(t => {
            const n = {...t, [isbn]: title};
            try { localStorage.setItem("ol_titles", JSON.stringify(n)); } catch {}
            return n;
          });
        })
        .catch(() => setTitles(t => ({...t, [isbn]: ""})));
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isbnKey]);
  return titles;
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
            {isProxy ? "aktif listeleme ortalaması (proxy)" : "avg_30d×0.25 + avg_90d×0.25 + avg_365d×0.50"}
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
            <PeriodBar label="Aktif min"    avg={p.avg_30d?.avg}  count={p.avg_30d?.count||0}  weight={0.33} C={C} />
            <PeriodBar label="Aktif ort."   avg={p.avg_90d?.avg}  count={p.avg_90d?.count||0}  weight={0.67} C={C} />
            <PeriodBar label="Aktif median" avg={p.avg_365d?.avg} count={p.avg_365d?.count||0} weight={1.00} C={C} />
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

function PricingTab({ isbns, C, push }) {
  const [selected, setSelected] = useState(isbns[0]||"");
  const [goodLimit, setGoodLimit] = useState(30);
  const [newLimit, setNewLimit] = useState(50);
  const [suggestedResult, setSuggestedResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [backoff, setBackoff] = useState(null);

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

  return (
    <div>
      {/* Finding API Backoff Banner */}
      {backoff?.active && (
        <div style={{background:"rgba(248,113,113,.08)",border:`1px solid ${C.red}`,borderRadius:8,padding:"12px 16px",marginBottom:16,display:"flex",alignItems:"center",gap:12}}>
          <span style={{fontSize:18}}>⏸</span>
          <div style={{flex:1}}>
            <div style={{color:C.red,fontSize:12,fontWeight:600}}>Finding API Backoff Aktif</div>
            <div style={{color:C.muted,fontSize:11,marginTop:2}}>
              Sold stats yaklaşık <b style={{color:C.orange}}>{Math.round(backoff.remaining_seconds/3600)}s</b> sonra yeniden denenecek · Gösterilen veriler stale/boş olabilir
            </div>
          </div>
          <button onClick={clearBackoff} style={{background:"none",border:`1px solid ${C.red}`,borderRadius:5,color:C.red,fontFamily:"var(--mono)",fontSize:11,padding:"5px 12px",cursor:"pointer",whiteSpace:"nowrap"}}>
            ✕ Temizle
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

      {/* Önerilen Fiyat Sorgulama */}
      <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:24}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:16}}>
          <ST C={C} style={{marginBottom:0}}>Önerilen Alım Fiyatı</ST>
          <div style={{fontSize:10,color:C.muted3,textAlign:"right",lineHeight:1.6}}>
            avg_30d×0.25 + avg_90d×0.25 + avg_365d×0.50<br/>
            Eksik → Browse proxy (aktif listeler)
          </div>
        </div>

        <div style={{display:"flex",gap:10,alignItems:"center",marginBottom:24}}>
          <select className="inp" value={selected} onChange={e=>setSelected(e.target.value)} style={{flex:1,maxWidth:300}}>
            {isbns.length===0 ? <option value="">Önce watchlist'e ISBN ekle</option> : isbns.map(isbn=><option key={isbn} value={isbn}>{isbn}</option>)}
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
      if (a.status==="fulfilled") setIsbns(a.value.items||[]);
      if (b.status==="fulfilled") setIntervals(b.value.intervals||{});
      if (c.status==="fulfilled") setStatus(c.value);
      if (d.status==="fulfilled") setAlertStats(d.value.stats||{});
      if (e.status==="fulfilled") setRunState(e.value.by_isbn||{});
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
  const titles = useBookTitles(isbns);

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
                <div style={{display:"grid",gridTemplateColumns:"repeat(4,1fr)",gap:14,marginBottom:28}}>
                  <StatCard C={C} icon="📚" label="Toplam ISBN" value={isbns.length} sub="watchlist'te" accent={C.accent}/>
                  <StatCard C={C} icon="🔄" label="Tarama Yapıldı" value={Object.keys(runState).length} sub="run_state kayıtları" accent={C.blue}/>
                  <StatCard C={C} icon="🎯" label="Toplam Alert" value={totalAlerts} sub="benzersiz item" accent={C.green}/>
                  <StatCard C={C} icon="🔔" label="Bot Token" value={status?.has_bot_token?"✓":"✗"} sub={status?.has_bot_token?"Telegram aktif":"Token yok"} accent={status?.has_bot_token?C.green:C.red}/>
                </div>
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
                {/* Tek ISBN Ekle */}
                <div style={{background:C.cardBg,border:`1px solid ${C.cardBorder}`,borderRadius:12,padding:20,marginBottom:16}}>
                  <ST C={C} style={{marginBottom:14}}>ISBN Ekle</ST>
                  <div style={{display:"flex",gap:10,flexWrap:"wrap",alignItems:"center"}}>
                    <input className="inp" placeholder="ISBN (örn: 9780132350884 veya 978-0132350884)" value={newIsbn} onChange={e=>setNewIsbn(e.target.value)} onKeyDown={e=>e.key==="Enter"&&addIsbn()} style={{...inp,width:260}}/>
                    <select className="inp" value={newInterval} onChange={e=>setNewInterval(e.target.value)} style={{...inp,width:130}}>
                      {[["30m","30 dk"],["1h","1 saat"],["4h","4 saat"],["8h","8 saat"],["12h","12 saat"],["24h","1 gün"],["48h","2 gün"]].map(([v,l])=><option key={v} value={v}>{l}</option>)}
                    </select>
                    <button className="add-btn" onClick={addIsbn} disabled={!newIsbn.trim()}>+ Ekle</button>
                    <button onClick={()=>setShowCsvImport(p=>!p)} style={{background:"none",border:`1px solid ${C.border}`,borderRadius:6,color:C.muted,fontFamily:"var(--mono)",fontSize:11,padding:"6px 12px",cursor:"pointer"}}>
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
                    <div key={isbn} className="row-item" style={{...row}}>
                      <div style={{flex:1}}>
                        <div style={{display:"flex",alignItems:"center",gap:8,flexWrap:"wrap"}}>
                          <span style={{fontFamily:"var(--sans)",fontSize:13,fontWeight:600}}>{isbn}</span>
                          {titles[isbn]&&<span style={{fontSize:12,color:C.muted,fontFamily:"var(--sans)"}}>— {titles[isbn]}</span>}
                          {titles[isbn]===null&&<span style={{fontSize:10,color:C.muted3}}>…</span>}
                          {alertStats[isbn]>0&&<span className="badge" style={{background:isDark?"#1a2a1a":"#f0fdf4",color:C.green}}>🎯 {alertStats[isbn]}</span>}
                        </div>
                        <div style={{fontSize:10,color:C.muted2,marginTop:3}}>{runState[isbn]?`son tarama: ${fmtTime(runState[isbn])}`:"henüz taranmadı"}</div>
                      </div>
                      {editing===isbn ? (
                        <div style={{display:"flex",gap:6,alignItems:"center"}}>
                          <input className="inp" style={{...inp,width:90,padding:"4px 8px",fontSize:12}} placeholder="4h / 30m" value={editVal} autoFocus onChange={e=>setEditVal(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")saveInterval(isbn,editVal);if(e.key==="Escape")setEditing(null);}}/>
                          <button className="add-btn" style={{padding:"4px 10px",fontSize:12}} onClick={()=>saveInterval(isbn,editVal)}>✓</button>
                          <button className="icon-btn" style={{fontSize:13}} onClick={()=>setEditing(null)}>✕</button>
                        </div>
                      ) : (
                        <button onClick={()=>{setEditing(isbn);setEditVal(fmtSecs(intervals[isbn])==="default"?"":fmtSecs(intervals[isbn]));}} style={{background:C.surface2,border:`1px solid ${C.border}`,borderRadius:4,color:intervals[isbn]?C.accent:C.muted,fontFamily:"var(--mono)",fontSize:12,padding:"3px 10px",cursor:"pointer"}}>
                          {fmtSecs(intervals[isbn])}
                        </button>
                      )}
                      <button className="icon-btn" onClick={()=>deleteIsbn(isbn)} style={{color:C.muted2,fontSize:18}}>×</button>
                    </div>
                  ))}
              </div>
            )}

            {tab==="pricing"&&<PricingTab isbns={isbns} C={C} push={push}/>}

            {tab==="alerts"&&(
              <div>
                <ST C={C} style={{marginBottom:16}}>Bildirim Geçmişi · {totalAlerts} item işaretlendi</ST>
                {Object.keys(alertStats).length===0
                  ? <div style={{border:`1px dashed ${C.border}`,borderRadius:8,padding:40,textAlign:"center",color:C.muted3,fontSize:12}}>Henüz hiç alert gönderilmedi.<br/><span style={{fontSize:10,marginTop:4,display:"block",color:C.muted3}}>Bulgular Telegram'a gönderilir.</span></div>
                  : Object.entries(alertStats).map(([isbn,count])=>(
                    <div key={isbn} style={{background:isDark?"#0d0d14":"#fff",border:`1px solid ${isDark?"#1a2a1a":"#d1fae5"}`,borderLeft:`3px solid ${C.green}`,borderRadius:8,padding:18,marginBottom:12}}>
                      <div style={{display:"flex",justifyContent:"space-between",alignItems:"center"}}>
                        <div>
                          <div style={{fontFamily:"var(--sans)",fontSize:14,fontWeight:600}}>{isbn}</div>
                          <div style={{fontSize:10,color:C.muted,marginTop:3}}>{count} benzersiz item Telegram'a bildirildi</div>
                        </div>
                        <div style={{display:"flex",alignItems:"center",gap:12}}>
                          <span style={{fontSize:28,fontWeight:600,color:C.green}}>{count}</span>
                          <button onClick={()=>clearAlerts(isbn)} style={{background:"none",border:`1px solid ${isDark?"#2a1a1a":"#fecaca"}`,color:C.red,padding:"5px 12px",borderRadius:5,fontSize:11,cursor:"pointer",fontFamily:"var(--mono)"}}>Temizle</button>
                        </div>
                      </div>
                      {runState[isbn]&&<div style={{fontSize:10,color:C.muted3,marginTop:10,borderTop:`1px solid ${C.border}`,paddingTop:8}}>Son tarama: {new Date(runState[isbn]*1000).toLocaleString("tr-TR")}</div>}
                    </div>
                  ))}
                <div style={{marginTop:20,padding:"16px 20px",background:C.cardBg,borderRadius:12,border:`1px solid ${C.cardBorder}`,fontSize:11,color:C.muted,lineHeight:2}}>
                  📬 Gerçek zamanlı deal bildirimleri Telegram'a gidiyor.<br/>
                  Bu ekran yalnızca <strong style={{color:C.text}}>dedup listesini</strong> gösterir.
                </div>
              </div>
            )}
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
