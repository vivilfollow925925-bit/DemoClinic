'use strict';
const products = [
  {id:'cup',name:'晨光陶瓷杯',category:'居家',desc:'温润釉面，适合日常茶饮；容量 350mL，礼盒款 450mL。',shape:'cup',color:'#e7e5ef',variants:[['白色',4000,8],['蓝色',4000,5],['礼盒',6000,3]]},
  {id:'lamp',name:'柔光台灯',category:'居家',desc:'柔和漫射光，陪伴阅读时光。使用本地模拟库存。',shape:'lamp',color:'#e5edda',variants:[['标准款',12000,6],['充电款',15000,2]]},
  {id:'bag',name:'轻行帆布包',category:'随行',desc:'宽肩带与内侧小口袋，收纳出门所需。',shape:'bag',color:'#f0e4d6',variants:[['小号',8000,5],['大号',10000,2]]},
  {id:'book',name:'留白笔记本',category:'文具',desc:'平摊装订，适合记录灵感与每日计划。',shape:'book',color:'#e0e8f1',variants:[['A5',5000,12],['B5',7000,4]]},
  {id:'tea',name:'山野茶罐',category:'居家',desc:'模拟茶叶礼品，密封罐装；不提供实际配送。',shape:'tin',color:'#e6ebdc',variants:[['清香',6000,7],['礼藏',9000,0]]},
  {id:'towel',name:'云感方巾',category:'居家',desc:'轻柔棉质，日常清洁和旅行都方便。',shape:'cloth',color:'#eee1dc',variants:[['单条',2000,15],['双条装',3000,4]]},
  {id:'audio',name:'随身耳机',category:'数码',desc:'轻巧入耳设计，收纳盒便于随身携带。',shape:'audio',color:'#dce9e7',variants:[['有线款',19900,3],['无线款',26000,0]]},
  {id:'bottle',name:'轻量保温瓶',category:'随行',desc:'简洁瓶身，可放入通勤包侧袋。',shape:'bottle',color:'#dde7ef',variants:[['350mL',10000,6],['500mL',12000,2]]},
  {id:'tray',name:'木纹托盘',category:'居家',desc:'为茶杯与小物留出一方整齐空间。',shape:'tray',color:'#ede4d7',variants:[['圆形',3990,9],['长方形',5990,1]]},
  {id:'candle',name:'暮色香氛',category:'居家',desc:'木质调居家摆件，商品信息仅用于交互测试。',shape:'candle',color:'#eae0e7',variants:[['小罐',4990,5],['大罐',6990,2]]}
].map(p=>({...p,variants:p.variants.map(([name,price,stock],i)=>({id:String(i),name,price,stock}))}));
const KEY='everyday-shop-v1';
const $=id=>document.getElementById(id);
const money=cents=>'¥'+(cents/100).toFixed(2);
const escapeHTML=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const product=id=>products.find(p=>p.id===id);
const variant=row=>product(row.pid).variants.find(v=>v.id===row.vid);
const key=row=>row.pid+':'+row.vid;
function initialState(){return {cart:[],orders:[],stock:Object.fromEntries(products.flatMap(p=>p.variants.map(v=>[p.id+':'+v.id,v.stock])))};}
function loadState(){
  try{const raw=localStorage.getItem(KEY);if(!raw)return initialState();const saved=JSON.parse(raw);
    if(!Array.isArray(saved.cart)||!Array.isArray(saved.orders)||!saved.stock)throw new Error('格式无效');
    saved.cart=saved.cart.map(row=>({...row,selected:row.selected||true}));return saved;
  }catch{return initialState();}
}
let state=loadState(),coupon=null,currentPage='shop',detailProduct=null,detailVariant='0',detailQty=1,submitting=false;
function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch{notify('浏览器未能保存数据，请检查本地存储权限。');}}
let toastTimer;
function notify(message){$('toast').textContent=message;$('toast').style.display='block';clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').style.display='none',3500);}
function artwork(p){
 const paths={cup:'<path d="M25 34h60v44q0 22-22 22H47q-22 0-22-22z"/><path d="M85 43h10q27 19 0 39H85" fill="none" stroke="currentColor" stroke-width="8"/><ellipse cx="55" cy="34" rx="30" ry="7" fill="#fff" opacity=".5"/>',lamp:'<path d="M28 54h65L76 15H45z"/><path d="M61 54v47M34 103h54" stroke="currentColor" stroke-width="8"/>',bag:'<path d="M25 40h70l-5 65H30z"/><path d="M44 42V27q17-25 34 0v15" fill="none" stroke="currentColor" stroke-width="7"/>',book:'<rect x="28" y="15" width="67" height="91" rx="7"/><path d="M40 15v91M52 43h29M52 57h22" stroke="#fff" stroke-width="4" opacity=".6"/>',tin:'<rect x="30" y="25" width="60" height="78" rx="9"/><ellipse cx="60" cy="25" rx="30" ry="9"/><path d="M45 55h30v28H45z" fill="#fff" opacity=".6"/>',cloth:'<path d="M24 25h63v74H24z" opacity=".6"/><path d="M40 39h63v68H40z"/><path d="M47 91h50" stroke="#fff" stroke-width="3"/>',audio:'<rect x="23" y="57" width="76" height="49" rx="21"/><path d="M38 15q-18 0-12 22h12v20M83 15q18 0 12 22H83v20" fill="none" stroke="currentColor" stroke-width="10"/>',bottle:'<rect x="45" y="12" width="30" height="18" rx="4"/><rect x="35" y="31" width="50" height="76" rx="15"/><path d="M44 50v38" stroke="#fff" stroke-width="4" opacity=".5"/>',tray:'<ellipse cx="60" cy="65" rx="49" ry="31"/><ellipse cx="60" cy="61" rx="40" ry="23" fill="none" stroke="#fff" stroke-width="3" opacity=".5"/>',candle:'<rect x="31" y="44" width="58" height="63" rx="6"/><path d="M60 42q-21-10 0-33 21 23 0 33" fill="#d6a24a"/><path d="M43 69h34v20H43z" fill="#fff" opacity=".6"/>'};
 return '<svg viewBox="0 0 120 120" aria-hidden="true" style="color:#50766a;fill:currentColor">'+paths[p.shape]+'</svg>';
}
function filterProducts(){
 const query=$('search').value.toLowerCase(),category=$('category').value,sort=$('sort').value;
 let list=products.filter(p=>(category==='全部'||p.category===category)&&p.name.toLowerCase().includes(query));
 if(sort!=='default')list.sort((a,b)=>{const x=String(a.variants[0].price/100),y=String(b.variants[0].price/100);return sort==='asc'?x.localeCompare(y):y.localeCompare(x);});
 $('resultCount').textContent=`找到 ${list.length} 件商品`;
 $('products').innerHTML=list.length?list.map(p=>`<article class="product"><div class="art" style="background:${p.color}">${artwork(p)}</div><div class="product-body"><span class="tag">${p.category}</span><h3>${p.name}</h3><span class="muted">${p.variants.length} 种规格可选</span><div class="product-foot"><span class="price">${money(p.variants[0].price)}<small> 起</small></span><button data-detail="${p.id}" ${p.id==='candle'?'disabled':''} aria-label="查看${p.name}">查看商品</button></div></div></article>`).join(''):'<div class="empty"><h3>没有找到相关商品</h3><p>试试其他关键词，或清除筛选。</p></div>';
}
function openDetail(pid){detailProduct=product(pid);detailVariant='0';detailQty=1;
 $('detailContent').innerHTML=`<div class="detail-art" style="background:${detailProduct.color}">${artwork(detailProduct)}</div><h2>${detailProduct.name}</h2><p>${detailProduct.desc}</p><div class="price" id="detailPrice">${money(detailProduct.variants[0].price)}</div><div id="specs" class="specs">${detailProduct.variants.map(v=>`<button data-spec="${v.id}" aria-pressed="${v.id==='0'}">${v.name}</button>`).join('')}</div><p class="stock" id="detailStock"></p><div class="quantity"><span>数量</span><button id="detailMinus" aria-label="减少购买数量">−</button><output id="detailQty">1</output><button id="detailPlus" aria-label="增加购买数量">+</button></div><button id="addToCart" class="primary wide">加入购物车</button>`;
 updateDetail();$('detail').showModal();
}
function updateDetail(){const stock=state.stock[detailProduct.id+':'+detailVariant];$('detailStock').textContent=`当前规格：${detailProduct.variants[Number(detailVariant)].name} · 库存 ${stock} 件`;$('detailQty').textContent=detailQty;$('addToCart').disabled=stock===0;$('addToCart').textContent=stock===0?'暂时售罄':'加入购物车';$('detailMinus').disabled=detailQty<=1;$('detailPlus').disabled=detailQty>=stock;document.querySelectorAll('[data-spec]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.spec===detailVariant)));}
function selectVariant(vid){detailVariant=vid;detailQty=1;updateDetail();}
function addToCart(){
 const p=detailProduct,v=p.variants[Number(detailVariant)];
 const row=state.cart.find(r=>r.pid===p.id&&variant(r).price===v.price);
 if((row?.qty||0)+detailQty>state.stock[p.id+':'+v.id])return notify('加入后的数量超过该规格库存。');
 if(row)row.qty+=detailQty;else state.cart.push({pid:p.id,vid:v.id,qty:detailQty,selected:true});
 refreshCoupon();save();renderCart();$('detail').close();notify('已加入购物车');
}
const subtotal=rows=>rows.reduce((sum,r)=>sum+variant(r).price*r.qty,0);
const selectedRows=()=>state.cart.filter(r=>r.selected);
function calculateCoupon(code){const total=subtotal(selectedRows()),basis=subtotal(state.cart);if(code==='SAVE20'&&basis>=20000)return {code,discount:Math.min(2000,total)};if(code==='NINE'&&basis>=10000)return {code,discount:Math.round(total/10)};return null;}
function refreshCoupon(){if(coupon)coupon=calculateCoupon(coupon.code);}
function totals(){const rows=selectedRows(),raw=subtotal(rows),discount=Math.min(raw,coupon?.discount||0),net=raw-discount,shipping=rows.length?(net>19900?0:1200):0;return {raw,discount,net,shipping,total:net+shipping};}
function summaryHTML(t){return `<div class="summary-row"><span>所选商品原价</span><span>${money(t.raw)}</span></div><div class="summary-row"><span>优惠</span><span>−${money(t.discount)}</span></div><div class="summary-row"><span>优惠后商品金额</span><span>${money(t.net)}</span></div><div class="summary-row"><span>运费</span><span>${money(t.shipping)}</span></div><div class="summary-row summary-total"><span>合计</span><span>${money(t.total)}</span></div>`;}
function renderCart(){
 $('cartCount').textContent=state.cart.reduce((n,r)=>n+r.qty,0);
 $('cartItems').innerHTML=state.cart.length?state.cart.map(r=>`<article class="cart-row" data-row="${key(r)}"><input type="checkbox" data-select="${key(r)}" aria-label="选择${product(r.pid).name}${variant(r).name}" ${r.selected?'checked':''}><div><h3>${product(r.pid).name}</h3><p class="muted">${variant(r).name} · 单价 ${money(variant(r).price)} · 库存 ${state.stock[key(r)]}</p><div class="quantity"><button data-minus="${key(r)}" aria-label="减少${product(r.pid).name}${variant(r).name}数量" ${r.qty===1?'disabled':''}>−</button><output>${r.qty}</output><button data-plus="${key(r)}" aria-label="增加${product(r.pid).name}${variant(r).name}数量">+</button></div></div><div class="cart-controls"><strong>${money(variant(r).price*r.qty)}</strong><br><button class="text-button" data-remove="${key(r)}">移除商品</button></div></article>`).join(''):'<div class="empty"><h3>购物车还是空的</h3><p>去商品页挑选一件日常好物吧。</p><button data-page="shop">去逛商品</button></div>';
 $('selectAll').checked=state.cart.length>0&&state.cart.every(r=>r.selected);$('selectAll').indeterminate=state.cart.some(r=>r.selected)&&!$('selectAll').checked;$('selectAll').disabled=!state.cart.length;
 $('couponStatus').textContent=coupon?`已使用 ${coupon.code}，优惠 ${money(coupon.discount)}`:'未使用优惠券';$('cartSummary').innerHTML=summaryHTML(totals());$('checkout').disabled=!selectedRows().length;
}
function changeQuantity(rowKey,delta){const row=state.cart.find(r=>key(r)===rowKey);row.qty=Math.max(1,row.qty+delta);refreshCoupon();save();renderCart();}
function removeItem(rowKey){state.cart=state.cart.filter(r=>key(r)!==rowKey);save();renderCart();notify('商品已移除');}
function showPage(page){currentPage=page;document.querySelectorAll('.page').forEach(el=>el.hidden=el.id!==page);document.querySelectorAll('nav [data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page===page));if(page==='cart')renderCart();if(page==='orders')renderOrders();window.scrollTo(0,0);}
function checkout(){if(!selectedRows().length)return notify('请先选择商品');$('checkoutLines').innerHTML=selectedRows().map(r=>`<div class="order-line">${product(r.pid).name} · ${variant(r).name} × ${r.qty}<br>${money(variant(r).price*r.qty)}</div>`).join('');$('checkoutSummary').innerHTML=summaryHTML(totals());$('formError').textContent='';showPage('checkoutPage');}
function submitOrder(event){
 event.preventDefault();if(submitting||currentPage!=='checkoutPage')return;
 const recipient=$('recipient').value.trim(),phone=$('phone').value.trim(),address=$('address').value.trim();
 if(!recipient||!address||!/^\d{11}$/.test(phone)){$('formError').textContent='请填写收件人、11 位测试手机号和测试地址。';return;}
 const rows=selectedRows();if(!rows.length)return notify('请先选择商品');
 if(rows.some(r=>!Number.isInteger(r.qty)||r.qty<1||r.qty>state.stock[key(r)])){$('formError').textContent='商品数量超过当前库存，请返回购物车调整。';return;}
 submitting=true;$('submitOrder').disabled=true;
 const order={id:'M'+Date.now().toString(36).toUpperCase()+'-'+(state.orders.length+1),created:new Date().toLocaleString('zh-CN'),status:'已提交',recipient,phone,address,coupon:coupon?.code||'',lines:rows.map(r=>({...r,name:product(r.pid).name,spec:variant(r).name,price:variant(r).price})),...totals()};
 rows.forEach(r=>state.stock[key(r)]-=r.qty);state.orders.unshift(order);state.cart=state.cart.filter(r=>!r.selected);coupon=null;save();renderCart();showPage('orders');$('checkoutForm').reset();notify('模拟订单已提交，无真实付款');submitting=false;$('submitOrder').disabled=false;
}
function renderOrders(){$('orderList').innerHTML=state.orders.length?state.orders.map(o=>`<article class="order-card"><div class="order-head"><strong>订单 ${o.id}</strong><span class="status">${o.status}</span></div><p class="muted">${o.created}</p><p>${o.lines.map(r=>`${r.name} · ${r.spec} × ${r.qty}`).join(' / ')}</p><p class="price">${money(o.total)}</p><div class="actions"><button data-order="${o.id}">查看订单详情</button><button data-cancel="${o.id}" ${o.status==='已取消'?'disabled':''}>取消订单</button></div></article>`).join(''):'<div class="empty"><h3>还没有模拟订单</h3><p>选购商品后，即可在这里查看。</p></div>';}
function openOrder(id){const o=state.orders.find(o=>o.id===id);$('orderContent').innerHTML=`<h2>订单详情</h2><p>${o.id} · ${o.status}</p><p>${escapeHTML(o.recipient)} / ${escapeHTML(o.phone)}<br>${escapeHTML(o.address)}</p>${o.lines.map(r=>`<div class="order-line">${r.name} · ${r.spec} × ${r.qty}<br>单价 ${money(r.price)} / 小计 ${money(r.price*r.qty)}</div>`).join('')}<p>优惠券：${o.coupon||'无'}</p>${summaryHTML(o)}`;$('orderDetail').showModal();}
function cancelOrder(id){const o=state.orders.find(o=>o.id===id);if(!o||o.status==='已取消')return;o.lines.forEach(r=>state.stock[key(r)]+=1);o.status='已取消';save();renderOrders();renderCart();notify('订单已取消，库存已恢复');}
function resetData(){state=initialState();coupon=null;save();$('search').value='';$('category').value='全部';$('sort').value='default';$('couponCode').value='';$('couponChoice').value='';$('checkoutForm').reset();$('resetDialog').close();filterProducts();renderCart();renderOrders();showPage('shop');notify('已恢复初始测试数据');}
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const d=b.dataset;if(d.page)showPage(d.page);if(d.detail)openDetail(d.detail);if(d.spec!==undefined)selectVariant(d.spec);if(d.plus)changeQuantity(d.plus,1);if(d.minus&&!d.minus.startsWith('towel:'))changeQuantity(d.minus,-1);if(d.remove)removeItem(d.remove);if(d.order)openOrder(d.order);if(d.cancel)cancelOrder(d.cancel);
 if(b.id==='detailPlus'){detailQty++;updateDetail();}if(b.id==='detailMinus'){detailQty=Math.max(1,detailQty-1);updateDetail();}if(b.id==='addToCart')addToCart();});
$('search').addEventListener('input',filterProducts);$('category').addEventListener('change',filterProducts);$('sort').addEventListener('change',filterProducts);
$('clearFilters').onclick=()=>{$('search').value='';$('category').value='全部';$('sort').value='default';filterProducts();};
$('cartItems').addEventListener('change',e=>{if(e.target.dataset.select){state.cart.find(r=>key(r)===e.target.dataset.select).selected=e.target.checked;refreshCoupon();save();renderCart();}});
$('selectAll').onchange=e=>{state.cart.forEach(r=>r.selected=e.target.checked);refreshCoupon();save();renderCart();};
$('couponChoice').onchange=e=>$('couponCode').value=e.target.value;
$('applyCoupon').onclick=()=>{const code=$('couponCode').value.trim().toUpperCase();if(!selectedRows().length)return notify('请先选择商品');if(!['SAVE20','NINE'].includes(code))return notify('优惠券不存在，请检查券码');const candidate=calculateCoupon(code);if(!candidate)return notify('所选商品金额未达到优惠券门槛');coupon=candidate;renderCart();notify('优惠券已使用');};
$('removeCoupon').onclick=()=>{coupon=null;renderCart();notify('已移除优惠券');};$('checkout').onclick=checkout;$('checkoutForm').onsubmit=submitOrder;
$('closeDetail').onclick=()=>$('detail').focus();$('closeOrder').onclick=()=>$('orderDetail').close();$('reset').onclick=()=>$('resetDialog').showModal();$('abortReset').onclick=()=>$('resetDialog').close();$('confirmReset').onclick=resetData;
document.querySelector('.brand').onclick=e=>{e.preventDefault();showPage('shop');};
filterProducts();renderCart();renderOrders();

