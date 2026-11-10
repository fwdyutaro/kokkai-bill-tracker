// 国会 法律案トラッカー — 描画ロジック（依存ライブラリなし）
// index.html の Content-Security-Policy（script-src 'self'）に合わせ、インラインの
// スクリプトやイベント属性は使わない。イベントはすべて addEventListener で登録する。
"use strict";

const $=s=>document.querySelector(s);
let allBills=[];   // 全会期のレコード
let bills=[];      // 選択中の会期に絞ったレコード（集計・一覧の対象）
const REPO_URL="https://github.com/fwdyutaro/kokkai-bill-tracker";
const STATUSES=["成立","審議中","継続審査","審査未了","廃案"];
const fmt=d=>{if(!d)return"";const[y,m,da]=String(d).split("-");return `${esc(y)}.${+m}.${+da}`};
const esc=s=>String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;").replace(/'/g,"&#39;");
// リンク先は http/https だけを許可する（データに javascript: 等が混入しても実行させない）
const safeUrl=u=>/^https?:\/\//i.test(String(u??""))?String(u):"#";
// CSSクラスに使う値は既知のものだけ通す
const statusClass=s=>STATUSES.includes(s)?s:"不明";
const tierClass=t=>[1,2,3,4].includes(Number(t))?Number(t):0;
// 検索用の正規化: 全角英数→半角、大文字→小文字、空白除去
const fold=s=>String(s??"").normalize("NFKC").toLowerCase().replace(/\s+/g,"");
// "221回" / 221 / "第221回" → 221（数値）。取れなければ null
const dietNo=v=>{const m=String(v??"").match(/\d+/);return m?Number(m[0]):null};
// "2026-07-25" → "令和8年7月25日"（令和以降のみ。それ以前は元の表記を返す）
function wareki(iso){
  if(!/^\d{4}-\d{2}-\d{2}$/.test(String(iso||"")))return String(iso||"");
  const[y,m,d]=iso.split("-").map(Number);
  if(y<2019)return `${y}年${m}月${d}日`;
  const r=y-2018;
  return `令和${r===1?"元":r}年${m}月${d}日`;
}
const sessionOf=diet=>(window.SESSIONS||[]).find(s=>dietNo(s.diet)===dietNo(diet))||null;
// 審議のタイムライン上の最後の動きの日付（並べ替え用）
const lastMove=b=>(b.timeline||[]).reduce((a,it)=>it.date&&it.date>a?it.date:a,b.submittedOn||"");
// 検索対象の文字列（件名・議案番号・所管・ステータス・タグ・概要）。レコードごとに1回だけ作る
const haystack=b=>b._hay||(b._hay=fold([b.title,b.no,b.ministry,b.status,b.statusDetail,
  (b.tags||[]).join(" "),b.summary].join(" ")));

// dashboard
function dash(){
  const c={成立:0,審議中:0,継続審査:0,審査未了:0,廃案:0};
  bills.forEach(b=>c[b.status]!==undefined&&c[b.status]++);
  $("#dash").innerHTML=`
    <div class="card s-合計"><div class="num">${bills.length}</div><div class="lbl">収録法案</div></div>
    <div class="card s-審議中"><div class="num">${c.審議中}</div><div class="lbl">審議中</div></div>
    <div class="card s-成立"><div class="num">${c.成立}</div><div class="lbl">成立・公布済</div></div>
    <div class="card s-継続審査"><div class="num">${c.継続審査}</div><div class="lbl">継続審査</div></div>
    <div class="card s-審査未了"><div class="num">${c.審査未了}</div><div class="lbl">審査未了</div></div>
    <div class="card s-廃案"><div class="num">${c.廃案}</div><div class="lbl">廃案</div></div>`;
}
// 会期セレクタ（データに含まれる会期を降順、最新をデフォルト選択）
function setupDiet(){
  const sel=$("#fDiet");
  const nos=[...new Set(allBills.map(b=>dietNo(b.diet)).filter(n=>n!==null))]
              .sort((a,b)=>b-a);
  sel.length=1;                       // 「すべての会期」だけ残す
  nos.forEach(n=>{
    const s=sessionOf(n),o=document.createElement("option");
    o.value=String(n);
    o.textContent=`第${n}回国会${s&&s.name?`（${s.name}）`:""}`;
    sel.appendChild(o);
  });
  if(nos.length)sel.value=String(nos[0]);   // 最新会期をデフォルト
}
// 選択中の会期でデータを絞り、集計・所管セレクタ・注記を作り直す
function applyDiet(){
  const v=$("#fDiet").value;
  bills=v?allBills.filter(b=>dietNo(b.diet)===Number(v)):allBills.slice();
  renderSessionNote(v);
  setupMinistry();
  dash();
  renderCabinetRate();
}
function renderSessionNote(v){
  const box=$("#sessionNote");
  const s=v?sessionOf(v):null;
  if(s&&s.to&&new Date(s.to+"T23:59:59+09:00")<new Date()){
    box.innerHTML=`<div class="session-note"><b>第${dietNo(s.diet)}回国会${s.name?`（${esc(s.name)}）`:""}は${esc(wareki(s.to))}に閉会しました。</b>
      閉会時点で議決に至らなかった法律案は「審査未了」、次の会期に引き継がれた法律案は「継続審査」として表示しています。</div>`;
  }else{
    box.innerHTML="";
  }
}
function renderCabinetRate(){
  const cabinet=bills.filter(b=>b.type==="閣法"),
        enacted=cabinet.filter(b=>b.status==="成立").length,
        total=cabinet.length,
        pct=total?Math.round(enacted/total*100):0,
        updated=String(window.BILLS_UPDATED_AT||"");
  let dateLabel="更新日不明";
  if(/^\d{4}-\d{2}-\d{2}$/.test(updated)){
    const [,month,day]=updated.split("-");
    dateLabel=`${Number(month)}月${Number(day)}日現在`;
  }
  $("#cabinetRate").innerHTML=`閣法成立率 <b>${pct}%（${enacted}/${total}）</b>（${dateLabel}）`;
}
// ministry filter options（データ読み込み後に再構築）
function setupMinistry(){
  const sel=$("#fMinis"),prev=sel.value;
  sel.length=1; // 「すべて」だけ残す
  const list=[...new Set(bills.map(b=>b.ministry))].sort();
  list.forEach(m=>{
    const o=document.createElement("option");o.textContent=m;o.value=m;sel.appendChild(o);
  });
  sel.value=list.includes(prev)?prev:"";   // 会期を跨いで存在しない所管はリセット
}

const SORTERS={
  submitted:(a,b)=>String(b.submittedOn||"").localeCompare(String(a.submittedOn||"")),
  moved:(a,b)=>lastMove(b).localeCompare(lastMove(a)),
};

function render(){
  const fs=$("#fStatus").value,ft=$("#fType").value,fm=$("#fMinis").value,
        // 空白区切りの語はすべて含むものに絞る（AND検索）
        terms=$("#fText").value.normalize("NFKC").toLowerCase().split(/\s+/).filter(Boolean),
        sorter=SORTERS[$("#fSort").value];
  const out=bills.filter(b=>
    (!fs||b.status===fs)&&(!ft||b.type===ft)&&(!fm||b.ministry===fm)&&
    terms.every(t=>haystack(b).includes(t)));
  if(sorter)out.sort(sorter);          // filter の結果は新しい配列なので元の並びは壊さない
  $("#count").textContent=`${out.length} 件`;
  $("#list").innerHTML=out.map(b=>`
    <div class="bill" data-id="${esc(b.id)}" tabindex="0" role="button"
         aria-label="${esc(b.no)} ${esc(b.shortTitle)}（${esc(b.status)}）の詳細を開く">
      <div class="top">
        <span class="badge b-${statusClass(b.status)}">${esc(b.status)}</span>
        <span class="badge type">${esc(b.no)}</span>
        <span class="badge minis">${esc(b.ministry)}</span>
        <span class="conf" style="margin-left:auto">成立確度 <b>${esc(b.confidence)}%</b></span>
      </div>
      <h3>${esc(b.shortTitle)}</h3>
      <div class="meta">${esc(b.dietLabel)}｜提出 ${fmt(b.submittedOn)}｜${esc(b.statusDetail)}</div>
      <div class="tags">${(b.tags||[]).map(t=>`<span class="tag" data-tag="${esc(t)}">#${esc(t)}</span>`).join("")}</div>
    </div>`).join("")||`<p style="color:var(--muted)">該当する法案がありません。</p>`;
}
["#fStatus","#fType","#fMinis","#fText","#fSort"].forEach(s=>$(s).addEventListener("input",render));
$("#fDiet").addEventListener("input",()=>{applyDiet();render();});
// リストのクリックはイベントデリゲーションで処理（タグはfilterTag、それ以外はドロワー展開）
$("#list").addEventListener("click",e=>{
  const tag=e.target.closest(".tag");
  if(tag){e.stopPropagation();filterTag(tag.dataset.tag);return;}
  const bill=e.target.closest(".bill");
  if(bill)showBill(bill.dataset.id);
});
// キーボード操作: カードにフォーカスして Enter / Space で詳細を開く
$("#list").addEventListener("keydown",e=>{
  if(e.key!=="Enter"&&e.key!==" ")return;
  const bill=e.target.closest(".bill");
  if(bill&&e.target===bill){e.preventDefault();showBill(bill.dataset.id);}
});

function tlClass(it){
  if(String(it.event||"").includes("待ち"))return"now";
  if(["可決","修正","成立"].includes(it.result))return"ok";   // 修正議決も可決の一形態
  if((it.result||"").includes("継続"))return"warn";
  return"";
}

// ---- 法案ごとの固定URL（#bill=<id>） --------------------------------------
// ドロワーの開閉はURLのハッシュを単一の情報源にする。共有・引用・「戻る」で閉じる、が揃う。
const billHash=id=>"#bill="+encodeURIComponent(id);
function hashBillId(){
  const m=location.hash.match(/^#bill=(.+)$/);
  if(!m)return null;
  try{return decodeURIComponent(m[1]);}catch(e){return null;}
}
function showBill(id){
  if(hashBillId()===id)syncDrawer();     // 同じ法案を開き直す場合は hashchange が起きない
  else location.hash=billHash(id);
}
function closeBill(){
  if(hashBillId()!==null){
    // ハッシュだけを消す（ページ先頭へのスクロールや余計な履歴を作らない）
    history.replaceState(null,"",location.pathname+location.search);
  }
  syncDrawer();
}
let lastFocus=null;
function syncDrawer(){
  const id=hashBillId();
  const b=id===null?null:allBills.find(x=>x.id===id);
  const drawer=$("#drawer"),overlay=$("#overlay");
  if(!b){
    const wasOpen=drawer.classList.contains("open");
    drawer.classList.remove("open");overlay.classList.remove("open");
    drawer.setAttribute("aria-hidden","true");
    if(wasOpen&&lastFocus&&document.contains(lastFocus))lastFocus.focus();
    lastFocus=null;
    return;
  }
  if(!drawer.classList.contains("open"))lastFocus=document.activeElement;
  renderBill(b);
  drawer.classList.add("open");overlay.classList.add("open");
  drawer.setAttribute("aria-hidden","false");
  drawer.scrollTop=0;
  const x=drawer.querySelector(".x");
  if(x)x.focus();
}
window.addEventListener("hashchange",syncDrawer);

function renderBill(b){
  const refs=b.refs||[];
  const kaigi=refs.filter(r=>r.cat==="会議録");        // 会議録は折りたたみにまとめる
  const refsByTier={};
  refs.filter(r=>r.cat!=="会議録").forEach(r=>{(refsByTier[r.tier]=refsByTier[r.tier]||[]).push(r)});
  const refHtml=[1,2,3,4].filter(t=>refsByTier[t]).map(t=>`
    ${refsByTier[t].map(r=>`
      <div class="ref">
        <div class="r1">
          <span class="cat tier${tierClass(r.tier)}">${esc(r.cat)}</span>
          <span class="pub">${esc(r.pub)}</span>
        </div>
        <a class="t" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${esc(r.title)} ↗</a>
        ${r.conf?`<div class="cf">自動紐付け確信度 <span class="bar"><i style="width:${Math.max(0,Math.min(100,Number(r.conf)||0))}%"></i></span> ${esc(r.conf)}%</div>`:``}
        ${r.confNote?`<div class="cf">⚙ ${esc(r.confNote)}</div>`:``}
        ${String(r.confNote||"").startsWith("自動紐付け")?`<a class="rm" target="_blank" rel="noopener noreferrer"
           href="${REPO_URL}/issues/new?template=remove-ref.yml&labels=removal&title=${encodeURIComponent('[取り下げ依頼] '+b.no)}&bill=${encodeURIComponent(b.no)}&bill_id=${encodeURIComponent(b.id||'')}&url=${encodeURIComponent(r.url)}&reftitle=${encodeURIComponent(r.title)}">この紐付けは関係ない → 取り下げ依頼</a>`:``}
      </div>`).join("")}`).join("");
  const kaigiHtml=kaigi.length?`
    <details class="kaigi">
      <summary><span class="cat tier1">会議録</span> 国会会議録 <b>${kaigi.length}</b>件 <span class="chev">▶</span></summary>
      <div class="kaigi-list">${kaigi.map(r=>`
        <a class="kaigi-item" href="${esc(safeUrl(r.url))}" target="_blank" rel="noopener noreferrer">${esc(r.title)} ↗</a>`).join("")}</div>
    </details>`:``;

  $("#drawer").innerHTML=`
    <div class="dhead">
      <button type="button" class="x" aria-label="詳細を閉じる">✕</button>
      <div class="top" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px">
        <span class="badge b-${statusClass(b.status)}">${esc(b.status)}・${esc(b.statusDetail)}</span>
        <span class="badge type">${esc(b.no)}</span>
        <span class="badge minis">${esc(b.ministry)}</span>
      </div>
      <h2 id="drawerTitle" style="margin:0;font-size:17px">${esc(b.title)}</h2>
      <p style="margin:6px 0 0;color:var(--muted);font-size:12.5px">
        ${esc(b.dietLabel)}｜提出 ${fmt(b.submittedOn)}｜成立確度（機械推定）<b style="color:var(--txt)">${esc(b.confidence)}%</b>
        ｜<a class="permalink" href="${esc(billHash(b.id))}">この法案へのリンク</a></p>
    </div>
    <div class="dbody">
      <div class="section">
        <h4>概要（AI生成）</h4>
        <div class="ai">${esc(b.summary)}<div class="note">⚠ ${esc(b.summaryNote)}</div></div>
      </div>
      <div class="section">
        <h4>審議のタイムライン</h4>
        <div class="tl">${(b.timeline||[]).map(it=>`
          <div class="it ${tlClass(it)}">
            <div class="d">${fmt(it.date)}</div>
            <div class="e"><span class="hs">${esc(it.house)}</span>${esc(it.event)}
              ${it.result?`<span class="res ${esc(it.result)}">【${esc(it.result)}】</span>`:``}</div>
          </div>`).join("")}</div>
      </div>
      <div class="section">
        <h4>参考情報（信頼度別）</h4>
        ${refHtml}
        ${kaigiHtml}
      </div>
      <div class="section">
        <h4>参考情報の提供</h4>
        <div class="ref">
          <a class="t" href="${REPO_URL}/issues/new?template=submit-ref.yml&title=${encodeURIComponent('[情報提供] '+b.no)}&bill=${encodeURIComponent(b.no)}&bill_id=${encodeURIComponent(b.id||'')}" target="_blank" rel="noopener noreferrer">＋ この法案の参考情報（NDLサーチ／CiNii／記事URL）を提供する ↗</a>
          <div class="cf">投稿（GitHub Issue）後、書誌情報と関連度を自動推定し、確認のうえ掲載します。</div>
        </div>
      </div>
    </div>`;
}
function filterTag(t){$("#fText").value=t;render();window.scrollTo({top:0,behavior:"smooth"});}
$("#drawer").addEventListener("click",e=>{if(e.target.closest(".x"))closeBill();});
$("#overlay").addEventListener("click",closeBill);
document.addEventListener("keydown",e=>{
  const drawer=$("#drawer");
  if(!drawer.classList.contains("open"))return;
  if(e.key==="Escape"){closeBill();return;}
  if(e.key!=="Tab")return;
  // ドロワーが開いている間は、フォーカスをドロワーの中で循環させる
  const items=[...drawer.querySelectorAll("a[href],button,summary,[tabindex]:not([tabindex='-1'])")];
  if(!items.length)return;
  const first=items[0],last=items[items.length-1];
  if(!drawer.contains(document.activeElement)){e.preventDefault();first.focus();}
  else if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
});

// データ読み込み（収集パイプライン出力）
// キャッシュ無効化のクエリは付けない。約700KBを毎回取り直さず、
// ブラウザの条件付きリクエスト（ETag）で更新の有無だけを確認させる。
(function loadData(){
  const s=document.createElement("script");
  s.src="data_collected.js";
  s.onload=()=>{
    if(!Array.isArray(window.BILLS)){$("#list").innerHTML=`<p style="color:var(--danger)">データを読み込めませんでした。</p>`;return;}
    allBills=window.BILLS;setupDiet();applyDiet();render();syncDrawer();
  };
  s.onerror=()=>{$("#list").innerHTML=`<p style="color:var(--danger)">データが見つかりません。</p>`;};
  document.body.appendChild(s);
})();
