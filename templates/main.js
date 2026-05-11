var AUTH_CONFIG={
  lavka:{reqUrl:'/api/auth/lavka/request-sms',cfmUrl:'/api/auth/lavka/confirm-sms',stUrl:'/api/auth/lavka/status',outUrl:'/api/auth/lavka/logout',trackId:true},
  samokat:{reqUrl:'/api/auth/samokat/request-sms',cfmUrl:'/api/auth/samokat/confirm-sms',stUrl:'/api/auth/samokat/status',outUrl:'/api/auth/samokat/logout',trackId:false},
  ozon:{reqUrl:'/api/auth/ozon/request-sms',cfmUrl:'/api/auth/ozon/confirm-sms',stUrl:'/api/auth/ozon/status',outUrl:'/api/auth/ozon/logout',trackId:false},
  pyaterochka:{reqUrl:'/api/auth/pyaterochka/request-sms',cfmUrl:'/api/auth/pyaterochka/confirm-sms',stUrl:'/api/auth/pyaterochka/status',outUrl:'/api/auth/pyaterochka/logout',trackId:false},
  magnit:{reqUrl:'/api/auth/magnit/request-sms',cfmUrl:'/api/auth/magnit/confirm-sms',stUrl:'/api/auth/magnit/status',outUrl:'/api/auth/magnit/logout',trackId:false},
  vkusvill:{reqUrl:'/api/auth/vkusvill/request-sms',cfmUrl:'/api/auth/vkusvill/confirm-sms',stUrl:'/api/auth/vkusvill/status',outUrl:'/api/auth/vkusvill/logout',trackId:false}
};
var _authState={};
window.addEventListener('DOMContentLoaded',function(){initAllAuthStatuses();});
async function initAllAuthStatuses(){
  try{
    var r=await fetch('/api/auth/all-status');
    var d=await r.json();
    if(d.success&&d.services) Object.keys(d.services).forEach(function(s){updateAuthUI(s,d.services[s]);});
  }catch(e){console.warn(e);}
}
function updateAuthUI(svc,info){
  var badge=document.getElementById('authBadge-'+svc);
  var sub=document.getElementById('authSubtitle-'+svc);
  var btnOut=document.getElementById('btnLogout-'+svc);
  var btnTog=document.getElementById('btnToggle-'+svc);
  if(!badge) return;
  if(info.authorized){
    badge.className='auth-status-badge authorized';
    badge.textContent='\u2705 \u0410\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d';
    var days=info.expires_in_days?' \u00b7 \u0442\u043e\u043a\u0435\u043d '+info.expires_in_days+' \u0434\u043d.':'';
    if(sub) sub.textContent='\u0410\u043a\u043a\u0430\u0443\u043d\u0442: '+(info.phone||'')+days;
    if(btnOut) btnOut.style.display='inline-flex';
    if(btnTog) btnTog.style.display='none';
    var f=document.getElementById('authForm-'+svc); if(f) f.classList.remove('show');
  }else{
    badge.className='auth-status-badge unauthorized';
    badge.textContent='\u26a0 \u041d\u0435 \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d';
    if(btnOut) btnOut.style.display='none';
    if(btnTog){btnTog.style.display='inline-flex'; btnTog.textContent='\u0412\u043e\u0439\u0442\u0438';}
  }
}
function toggleAuthForm(svc){
  var f=document.getElementById('authForm-'+svc);
  var b=document.getElementById('btnToggle-'+svc);
  if(!f) return;
  if(f.classList.contains('show')){f.classList.remove('show'); if(b) b.textContent='\u0412\u043e\u0439\u0442\u0438';}
  else{f.classList.add('show'); if(b) b.textContent='\u0421\u043a\u0440\u044b\u0442\u044c'; resetAuthForm(svc);}
}
function resetAuthForm(svc){
  var s1=document.getElementById('authStep1-'+svc);
  var s2=document.getElementById('authStep2-'+svc);
  var c=document.getElementById('authCode-'+svc);
  if(s1) s1.classList.add('active'); if(s2) s2.classList.remove('active');
  if(c) c.value=''; hideAuthMsg(svc); _authState[svc]={};
}
function showAuthMsg(svc,type,text){var e=document.getElementById('authMsg-'+svc); if(e){e.className='auth-msg show '+type; e.textContent=text;}}
function hideAuthMsg(svc){var e=document.getElementById('authMsg-'+svc); if(e) e.className='auth-msg';}
async function requestSms(svc){
  var ph=(document.getElementById('authPhone-'+svc)||{}).value||''; ph=ph.trim();
  if(!ph){document.getElementById('authPhone-'+svc).focus(); return;}
  var btn=document.getElementById('btnSms-'+svc);
  if(btn){btn.disabled=true; btn.textContent='\u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0435\u043c...';}
  hideAuthMsg(svc);
  try{
    var r=await fetch(AUTH_CONFIG[svc].reqUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:ph})});
    var d=await r.json();
    if(d.success){
      _authState[svc]={phone:d.phone,track_id:d.track_id||'',csrf_token:d.csrf_token||''};
      var s1=document.getElementById('authStep1-'+svc);
      var s2=document.getElementById('authStep2-'+svc);
      if(s1) s1.classList.remove('active'); if(s2) s2.classList.add('active');
      var h=document.getElementById('authCodeHint-'+svc);
      if(h) h.textContent='\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u0434, \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043d\u044b\u0439 \u043d\u0430 '+d.phone;
      showAuthMsg(svc,'info','SMS \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u043e \u043d\u0430 '+d.phone);
      setTimeout(function(){var c=document.getElementById('authCode-'+svc); if(c) c.focus();},100);
    }else{
      showAuthMsg(svc,'error','\u274c '+(d.error||'\u041e\u0448\u0438\u0431\u043a\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u043a\u0438 SMS'));
    }
  }catch(e){
    showAuthMsg(svc,'error','\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u044f: '+e.message);
  }finally{
    if(btn){btn.disabled=false; btn.textContent='\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c SMS';}
  }
}
async function confirmSms(svc){
  var code=(document.getElementById('authCode-'+svc)||{}).value||''; code=code.trim();
  if(!code){document.getElementById('authCode-'+svc).focus(); return;}
  var btn=document.getElementById('btnConfirm-'+svc);
  if(btn){btn.disabled=true; btn.textContent='\u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c...';}
  hideAuthMsg(svc);
  var st=_authState[svc]||{};
  var body={phone:st.phone||'',code:code};
  if(AUTH_CONFIG[svc].trackId){body.track_id=st.track_id||''; body.csrf_token=st.csrf_token||'';}
  try{
    var r=await fetch(AUTH_CONFIG[svc].cfmUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    var d=await r.json();
    if(d.success){
      showAuthMsg(svc,'success','\u2705 '+(d.message||'\u0410\u0432\u0442\u043e\u0440\u0438\u0437\u0430\u0446\u0438\u044f \u0443\u0441\u043f\u0435\u0448\u043d\u0430!'));
      setTimeout(function(){var f=document.getElementById('authForm-'+svc); if(f) f.classList.remove('show'); initAllAuthStatuses();},1500);
    }else{
      showAuthMsg(svc,'error','\u274c '+(d.error||'\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043a\u043e\u0434'));
    }
  }catch(e){
    showAuthMsg(svc,'error','\u274c \u041e\u0448\u0438\u0431\u043a\u0430: '+e.message);
  }finally{
    if(btn){btn.disabled=false; btn.textContent='\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c';}
  }
}
async function authLogout(svc){
  try{await fetch(AUTH_CONFIG[svc].outUrl,{method:'POST'}); updateAuthUI(svc,{authorized:false});}
  catch(e){console.warn(e);}
}
function setAddress(addr){document.getElementById('addressInput').value=addr; document.getElementById('addressInput').focus();}
function showStatus(type,html){var bar=document.getElementById('statusBar'),sp=document.getElementById('statusSpinner'),tx=document.getElementById('statusText'); bar.className='status-bar show '+type; sp.style.display=type==='loading'?'block':'none'; tx.innerHTML=html;}
function hideStatus(){document.getElementById('statusBar').className='status-bar';}
function showResultAddress(data){document.getElementById('addrValue').textContent=data.display_name; document.getElementById('addrCoords').textContent='\u041a\u043e\u043e\u0440\u0434\u0438\u043d\u0430\u0442\u044b: '+data.lat.toFixed(6)+', '+data.lon.toFixed(6); document.getElementById('resultAddress').className='result-address show';}
function formatTiers(tiers){
  if(!tiers||!tiers.length) return '<span class="badge badge-na">\u2014</span>';
  var rows=tiers.map(function(t){
    var s=t.cart_sum.toLocaleString('ru-RU')+' \u20bd';
    var p;
    if(!t.available) p='<span class="tier-price unavail">\u043d\u0438\u0436\u0435 \u043c\u0438\u043d\u0438\u043c\u0443\u043c\u0430</span>';
    else if(t.delivery_price===0) p='<span class="tier-price free">\u2705 0 \u20bd</span>';
    else p='<span class="tier-price paid">'+Number(t.delivery_price).toLocaleString('ru-RU')+' \u20bd</span>';
    return '<div class="tier-row"><span class="tier-sum">'+s+'</span><span class="tier-arrow">\u2192</span>'+p+'</div>';
  });
  return '<div class="tiers-grid">'+rows.join('')+'</div>';
}
function formatAmount(v){if(v===null||v===undefined) return '<span class="badge badge-na">\u2014</span>'; return '<div class="price-value">'+Number(v).toLocaleString('ru-RU')+' \u20bd</div>';}
function formatFreeFrom(v){if(v===null||v===undefined) return '<span class="badge badge-na">\u041d\u0435\u0442</span>'; return '<div class="price-value">'+Number(v).toLocaleString('ru-RU')+' \u20bd</div>';}
function formatTime(v){if(!v) return '<span class="badge badge-na">\u2014</span>'; return '<span class="time-badge">\u23f1 '+v+'</span>';}
function formatFee(v){if(v===null||v===undefined) return '<span class="badge badge-na">\u2014</span>'; var n=Number(v); if(n===0) return '<span class="badge badge-free">\u2705 0 \u20bd</span>'; return '<div class="price-value">'+n.toLocaleString('ru-RU')+' \u20bd</div>';}
function getLogoClass(s){var m={'\u0421\u0430\u043c\u043e\u043a\u0430\u0442':'logo-samokat','\u042f\u043d\u0434\u0435\u043a\u0441 \u041b\u0430\u0432\u043a\u0430':'logo-lavka','Ozon Fresh':'logo-ozon','\u041f\u044f\u0442\u0451\u0440\u043e\u0447\u043a\u0430':'logo-pyat','\u041c\u0430\u0433\u043d\u0438\u0442':'logo-magnit','\u0412\u043a\u0443\u0441\u0412\u0438\u043b\u043b':'logo-vkusvill'}; return m[s]||'';}
function renderRow(item){
  if(!item.available) return '<tr><td><div class="service-cell"><div class="service-logo '+getLogoClass(item.service)+'">'+(item.logo||'?')+'</div><div><div class="service-name">'+item.service+'</div></div></div></td><td colspan="6"><span class="badge badge-unavailable">\uD83D\uDEAB \u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0438</span></td></tr>';
  var sc,sl;
  if(item.auth_used){sc='source-auth';sl='\u25cf \u0410\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d';}
  else if(item.data_source==='api'){sc='source-api';sl='\u25cf API';}
  else{sc='source-public';sl='\u25cf \u041f\u0443\u0431\u043b\u0438\u0447\u043d\u044b\u0435';}
  var nh=item.note?'<div class="service-note">'+item.note+'</div>':'';
  return '<tr><td><div class="service-cell"><div class="service-logo '+getLogoClass(item.service)+'">'+(item.logo||'\uD83D\uDED2')+'</div><div><div class="service-name">'+item.service+'</div><span class="source-badge '+sc+'">'+sl+'</span>'+nh+'</div></div></td><td class="tiers-cell">'+formatTiers(item.delivery_tiers)+'</td><td>'+formatAmount(item.min_order)+'</td><td>'+formatFreeFrom(item.free_from)+'</td><td>'+formatTime(item.delivery_time)+'</td><td class="pkg-cell">'+formatFee(item.packaging_price)+'</td><td class="asm-cell">'+formatFee(item.assembly_price)+'</td></tr>';
}
async function checkDelivery(){
  var input=document.getElementById('addressInput');
  var btn=document.getElementById('checkBtn');
  var address=input.value.trim();
  if(!address){input.focus(); input.style.borderColor='#ef4444'; setTimeout(function(){input.style.borderColor='';},1500); return;}
  btn.disabled=true; btn.textContent='\u23f3 \u041f\u0440\u043e\u0432\u0435\u0440\u044f\u0435\u043c...';
  document.getElementById('tableCard').className='table-card';
  document.getElementById('resultAddress').className='result-address';
  document.getElementById('legend').className='legend';
  showStatus('loading','\u0417\u0430\u043f\u0440\u0430\u0448\u0438\u0432\u0430\u0435\u043c \u0434\u0430\u043d\u043d\u044b\u0435 \u0434\u043b\u044f \u0430\u0434\u0440\u0435\u0441\u0430 <strong>'+address+'</strong>\u2026 (\u043c\u043e\u0436\u0435\u0442 \u0437\u0430\u043d\u044f\u0442\u044c \u0434\u043e 30 \u0441\u0435\u043a)');
  try{
    var r=await fetch('/api/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({address:address})});
    var d=await r.json();
    if(!d.success){showStatus('error','\u274c '+d.error); return;}
    showResultAddress(d);
    document.getElementById('tableBody').innerHTML=d.results.map(renderRow).join('');
    document.getElementById('tableCard').className='table-card show';
    document.getElementById('legend').className='legend show';
    hideStatus();
    if(d.auth_all) Object.keys(d.auth_all).forEach(function(s){updateAuthUI(s,d.auth_all[s]);});
  }catch(err){
    showStatus('error','\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0441\u043e\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u044f: '+err.message);
  }finally{
    btn.disabled=false; btn.textContent='\uD83D\uDD0D \u041f\u0440\u043e\u0432\u0435\u0440\u0438\u0442\u044c';
  }
}
document.addEventListener('DOMContentLoaded',function(){
  document.getElementById('addressInput').addEventListener('keydown',function(e){if(e.key==='Enter') checkDelivery();});
});
