// The browser displays PLC-validated data exposed by the existing OPC UA HMI.
// Plant positions are sampled, never predicted from drive speed in this view.
const $ = (id) => document.getElementById(id);
const svgNS = 'http://www.w3.org/2000/svg';
const svg = (name, attrs = {}) => {
  const el = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  return el;
};
const text = (id, value) => { $(id).textContent = String(value); };
const validNumber = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;
const zoneNames = ['NONE', 'APPROACH', 'DECISION', 'PREMERGE', 'MERGE', 'OUTBOUND', 'TERMINAL', 'RECIRC TAIL'];
const motionNames = ['NONE', 'MOVING', 'HELD DOWNSTREAM', 'HELD MERGE', 'DRIVE STOPPED', 'AWAITING ROUTE', 'OUTBOUND', 'TERMINAL'];
const holdNames = ['NONE', 'ZONE FULL', 'MERGE CAPACITY', 'DRIVE OFF', 'ROUTE PENDING', 'CHUTE FULL'];
const eventNames = ['WAIT', 'INDUCT', 'TUNNEL', 'DIVERT', 'TRAILER', 'RECIRC', 'FAILED CONFIRMATION'];
const beltNames = {1:'INDUCT 1', 5:'INDUCT 2', 6:'INDUCT 3', 2:'OUTBOUND 1', 3:'OUTBOUND 2', 4:'OUTBOUND 3'};
const beltY = {1:75, 5:145, 6:215, 2:303, 3:363, 4:423};
const colors = ['#86afcf', '#a9c98f', '#d8b88d'];
const packages = new Map();
const previous = new Map();
let selectedId = '';
let lastGood = 0;
let lastData = null;
let pollBusy = false;
let lastConnection = '';

function addObservation(message) {
  const list = $('activity-list');
  if (list.children.length === 1 && list.firstElementChild.textContent === 'Waiting for live telemetry.') list.replaceChildren();
  const li = document.createElement('li');
  const stamp = document.createElement('time');
  stamp.textContent = new Date().toLocaleTimeString();
  li.append(stamp, document.createTextNode(message));
  list.prepend(li);
  while (list.children.length > 12) list.lastElementChild.remove();
}

function drawStaticMap() {
  const tracks = $('tracks');
  for (const [belt, y] of Object.entries(beltY)) {
    tracks.append(svg('rect', {x:153,y:y-18,width:781,height:36,rx:4,fill:'url(#belt)',stroke:'#385262'}));
    const label = svg('text', {x:137,y:y+4,'text-anchor':'end',class:'track-label'});
    label.textContent = beltNames[belt];
    tracks.append(label);
    for (let cell = 0; cell < 20; cell++) tracks.append(svg('line', {x1:163+cell*39,x2:163+cell*39,y1:y-15,y2:y+15,stroke:'#405260','stroke-opacity':cell%5===0?.8:.3}));
  }
  for (const [i,y] of [75,145,215].entries()) {
    const scanner = svg('text',{x:550,y:y-24,'text-anchor':'middle',class:'mark-label'});scanner.textContent='CAMERA '+(i+1);tracks.append(scanner);
    tracks.append(svg('line',{x1:553,x2:553,y1:y-17,y2:y+17,stroke:'#75a49e','stroke-width':2}));
    tracks.append(svg('path',{d:`M 934 ${y} C 978 ${y} 952 303 934 303`,class:'bridge'}));
  }
  for (const [i,y] of [303,363,423].entries()) {
    const outlet = svg('text',{x:954,y:y+4,class:'mark-label'});outlet.textContent='DOORS '+(i*3+1)+'–'+(i*3+3);tracks.append(outlet);
  }
  const join = svg('text',{x:970,y:268,class:'mark-label'});join.textContent='SHARED MERGE';tracks.append(join);
}

function addPackage(id, lane) {
  const group = svg('g',{class:'package',tabindex:0,role:'button','aria-label':'Inspect package '+id});
  group.append(svg('rect',{x:-20,y:-13,width:40,height:26,rx:5,fill:colors[lane-1]}));
  const label = svg('text',{'text-anchor':'middle',y:4}); label.textContent=id.split('-').at(-2);group.append(label);
  group.addEventListener('click',()=>{selectedId=id;updateSelection();});
  group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectedId=id;updateSelection();}});
  $('packages').append(group);
  packages.set(id,{group,last:null});
  return packages.get(id);
}

function decodeRows(d) {
  const decoded = new Map();
  if (!d.plant_mode) return decoded;
  for(let i=0;i<3;i++){
    const r=d.plant_rows?.[i];
    const status=validNumber(d.plant_status?.[i]);
    const lane=validNumber(d.plant_lane?.[i]);
    if(!Array.isArray(r)||r.length<9||status===0||r[3]===0||lane<1||lane>3) continue;
    const id=`l${lane}-${validNumber(r[0])+validNumber(r[1])*30000}-${r[2]}-${r[3]}-${r[4]}`;
    const zone=d.zone_rows?.[i];
    const photoeye=d.photoeye_rows?.[i];
    const zoneValid=!d.accumulation_mode||(Array.isArray(zone)&&zone.length>=6&&zone[5]===1&&zone[4]!==0&&d.zone_age<=15);
    const peValid=!d.photoeye_mode||(Array.isArray(photoeye)&&photoeye.length>=8&&r.slice(0,5).every((value,index)=>value===photoeye[index]));
    const stale=status!==1||d.plant_fault!==0||!zoneValid||!peValid||!d.connected;
    decoded.set(id,{id,lane,slot:i+1,belt:r[5],pos:r[6]/10,event:r[7],actual:r[8],status,age:d.plant_age?.[i],zone,photoeye,zoneValid,peValid,stale,hold:!!d.accumulation_mode&&zoneValid&&[2,3,4,5].includes(zone[1])});
  }
  return decoded;
}

function drawPackages(rows, fresh) {
  for(const [id,row] of rows){
    const existing=packages.get(id);
    if(!fresh&&!existing)continue;
    const entry=existing||addPackage(id,row.lane);
    // An invalid/unknown belt cannot be converted into a plausible coordinate.
    if((!existing || (fresh&&!row.stale)) && beltY[row.belt]!==undefined && Number.isFinite(row.pos)){
      const x=172+Math.max(0,Math.min(19,row.pos))*39;
      entry.group.setAttribute('transform',`translate(${x} ${beltY[row.belt]})`);
      entry.last=row;
    }
    entry.group.classList.toggle('held',row.hold&&!row.stale&&fresh);
    entry.group.classList.toggle('stale',row.stale||!fresh);
    entry.group.classList.toggle('selected',selectedId===id);
  }
  // Do not discard the last known positions on a failed or stale poll.
  if(!fresh)return;
  for(const [id,entry] of packages){
    if(rows.has(id))continue;
    // An occupied slot with an unavailable identity keeps its last location.
    if(entry.last && lastData?.plant_status?.[entry.last.slot-1]!==0){entry.group.classList.add('stale');continue;}
    entry.group.remove();packages.delete(id);
  }
  if(selectedId&&!packages.has(selectedId))selectedId='';
}

function updateSelection() {
  for(const [id,entry] of packages)entry.group.classList.toggle('selected',id===selectedId);
  const row=packages.get(selectedId)?.last;
  const detail=$('package-detail');
  if(!row){detail.className='empty-detail';detail.textContent='Select a package on the sorter map.';return;}
  detail.className='package-info';detail.replaceChildren();
  const identity=document.createElement('div');identity.className='identity';identity.textContent=row.id;detail.append(identity);
  const fields=[['SLOT / LANE',`${row.slot} / ${row.lane}`],['BELT',beltNames[row.belt]||'UNKNOWN'],['POSITION',Number.isFinite(row.pos)?`${row.pos.toFixed(1)} cells`:'UNAVAILABLE'],['TELEMETRY',row.stale||Date.now()-lastGood>1500?'STALE / UNAVAILABLE':`LIVE · ${row.age} scans`],['EVENT',eventNames[row.event]||'UNKNOWN'],['ZONE',row.zoneValid?zoneNames[row.zone?.[0]]||'UNKNOWN':'UNAVAILABLE'],['MOTION',row.zoneValid?motionNames[row.zone?.[1]]||'UNKNOWN':'UNAVAILABLE'],['HOLD REASON',row.zoneValid?holdNames[row.zone?.[2]]||'UNKNOWN':'UNAVAILABLE'],['HOLD DWELL',row.zoneValid?`${(row.zone?.[3]/10).toFixed(1)} s`:'UNAVAILABLE'],['PHOTOEYES',row.peValid&&row.photoeye?`raw ${row.photoeye[5]} · filtered ${row.photoeye[6]}`:'UNAVAILABLE']];
  for(const [label,value] of fields){const line=document.createElement('div');line.className='detail-row';const k=document.createElement('span');k.textContent=label;const v=document.createElement('strong');v.textContent=value;line.append(k,v);detail.append(line);}
}

function drawTrailers(d) {
  const grid=$('trailer-grid');grid.replaceChildren();
  for(let i=0;i<9;i++){
    const card=document.createElement('div');card.className='trailer'+(i===1&&d.chute?.measured_trailer===2?' measured':'');
    const label=document.createElement('span');label.className='label';label.textContent='TRAILER '+(i+1);
    const count=document.createElement('strong');count.textContent=String(d.trailer?.[i]??'—');
    const extra=document.createElement('small');extra.textContent=i===1&&d.chute?.measured_trailer===2?'MEASURED CHUTE':`${d.bad?.[i]??0} wrong`;
    card.append(label,count,extra);grid.append(card);
  }
}

function drawDrives(d){const grid=$('drive-grid');grid.replaceChildren();for(const v of d.drives||[]){const card=document.createElement('div');card.className='drive'+(v.fault?' fault':'');const name=document.createElement('b');name.textContent=v.name;const rpm=document.createElement('strong');rpm.textContent=String(v.rpm??'—');const unit=document.createElement('small');unit.textContent=v.fault?`FAULT ${v.fault}`:`RPM · target ${v.ref}`;const bar=document.createElement('div');bar.className='drive-bar';const fill=document.createElement('div');fill.style.width=Math.min(100,Math.max(0,validNumber(v.rpm)/1750*100))+'%';bar.append(fill);card.append(name,rpm,unit,bar);grid.append(card);}}

async function action(path,button){button.disabled=true;try{const response=await fetch(path,{method:'POST'});const result=await response.json();if(!response.ok||!result.ok)throw Error(result.err||`Command rejected (${response.status})`);addObservation('Operator command accepted: '+path.split('/').at(-1)+'.');}catch(error){addObservation('Operator command failed: '+error.message);}finally{button.disabled=false;}}

function drawChute(d,fresh){const c=d.chute||{};const measured=c.measured_trailer===2;const reliable=fresh&&measured&&c.poll_fresh&&c.quality===1;const occupied=validNumber(c.occupied),capacity=validNumber(c.capacity);text('chute-quality',reliable?'PLC SAMPLE LIVE':'UNAVAILABLE');text('chute-count',reliable?`${occupied} / ${capacity} occupied`:'— / — occupied');const fill=$('chute-fill');fill.style.width=reliable&&capacity?Math.min(100,occupied/capacity*100)+'%':'0%';fill.classList.toggle('full',reliable&&occupied>=capacity);const status=!measured?'Measured sensor is disabled.':!reliable?'Holding last known state; sample is stale or unavailable.':({0:'Ready to accept a package.',1:'Full. Acknowledge before emptying.',2:'Acknowledged. Empty the chute to clear the sensor.',3:'Empty. Waiting for operator Resume.',4:'Sensor unavailable; admission closed.'}[c.state]||'State unknown.');text('chute-state',status);const actions=$('chute-actions');actions.replaceChildren();const choice=reliable?({1:'acknowledge',2:'empty',3:'resume'}[c.state]):null;if(choice){const button=document.createElement('button');button.textContent=choice.toUpperCase();button.onclick=()=>action('/chute/action/'+choice,button);actions.append(button);}}

function drawAlarms(d,fresh){const alarms=[];if(!fresh)alarms.push('TELEMETRY STALE — POSITIONS FROZEN');if(d.alarms?.jam)alarms.push('JAM');if(d.alarms?.coll)alarms.push('COLLISION');if(d.alarms?.noread)alarms.push('NO READ');if(d.alarms?.nohome)alarms.push('NO HOME');if(d.plant_fault)alarms.push('PLANT FAULT '+d.plant_fault);if(d.zone_fault)alarms.push('ZONE VALIDATION FAULT '+d.zone_fault);if(d.photoeye_fault_mask)alarms.push(`PHOTOEYE LANE ${d.photoeye_fault_lane} SENSOR ${d.photoeye_fault_sensor}`);if(d.xle_liveness===1||d.xle_liveness===2)alarms.push('XLE HEARTBEAT LOST');if(d.chute?.measured_trailer===2&&(!d.chute.poll_fresh||d.chute.quality!==1||d.chute.state===4))alarms.push('CHUTE SENSOR UNAVAILABLE');else if(d.chute?.state===1||d.chute?.state===2)alarms.push('TRAILER 2 CHUTE FULL');const strip=$('alarm-strip');strip.className='alarm-strip'+(alarms.length?' active':'');strip.replaceChildren();const dot=document.createElement('span');dot.className='quiet-dot';strip.append(dot);if(alarms.length){for(const item of alarms){const span=document.createElement('span');span.className='alarm-item';span.textContent=item;strip.append(span);}}else strip.append('No active process alarms');}

function observe(rows,d){for(const [id,row] of rows){const old=previous.get(id);if(!old)addObservation(`${id} observed at ${beltNames[row.belt]||'belt '+row.belt}.`);else if(old.hold!==row.hold)addObservation(`${id} ${row.hold?'entered '+(holdNames[row.zone?.[2]]||'a hold'):'left its hold'}.`);previous.set(id,{hold:row.hold});}for(const id of previous.keys())if(!rows.has(id))previous.delete(id);}

function render(d,fresh){lastData=d;const rows=decodeRows(d);if(fresh)observe(rows,d);drawPackages(rows,fresh);updateSelection();drawAlarms(d,fresh);text('stat-inducted',d.inducted??'—');text('stat-loaded',d.trailer?.reduce((a,v)=>a+validNumber(v),0)??'—');text('stat-flight',rows.size);text('stat-rate',d.load_rate??'—');text('stat-recirc',d.recirc??'—');text('mode-pill',d.plant_mode?'PLANT MODE': 'PLC CELL MODE');text('status-line',fresh?`${d.run?'Sorting active':'Sorter stopped'} · ${d.accumulation_mode?'accumulation on':'accumulation off'} · ${rows.size} occupied PLC slots`:'Telemetry unavailable. Package positions are frozen at their last validated samples.');const button=$('operator-toggle');button.disabled=!fresh;button.textContent=d.run?'STOP SORTER':'START SORTER';button.classList.toggle('stop',!!d.run);drawTrailers(d);drawDrives(d);drawChute(d,fresh);}

async function poll(){if(pollBusy)return;pollBusy=true;try{const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),1200);let response;try{response=await fetch('/api',{signal:controller.signal,cache:'no-store'});}finally{clearTimeout(timer);}if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();const fresh=!!data.connected;if(fresh)lastGood=Date.now();render(data,fresh);const state=fresh?'LIVE':'UA UNAVAILABLE';if(state!==lastConnection){addObservation(state==='LIVE'?'OPC UA telemetry connected.':'OPC UA telemetry unavailable.');lastConnection=state;}const indicator=$('connection');indicator.textContent=state;indicator.className='connection'+(fresh?'':' offline');}catch(error){if(lastData)render(lastData,false);const indicator=$('connection');indicator.textContent='TELEMETRY UNAVAILABLE';indicator.className='connection offline';if(lastConnection!=='UNAVAILABLE'){addObservation('HMI API unavailable; positions frozen.');lastConnection='UNAVAILABLE';}text('status-line','HMI API unavailable. Package positions are frozen at their last validated samples.');}finally{pollBusy=false;}}

$('operator-toggle').addEventListener('click',()=>{const button=$('operator-toggle');if(!lastData||button.disabled)return;action(`/cmd/run/${lastData.run?0:1}`,button);});
drawStaticMap();setInterval(()=>{text('clock',new Date().toLocaleTimeString());if(lastData&&Date.now()-lastGood>1500){for(const entry of packages.values())entry.group.classList.add('stale');updateSelection();const indicator=$('connection');indicator.textContent='TELEMETRY STALE';indicator.className='connection stale';$('operator-toggle').disabled=true;}},500);setInterval(poll,250);poll();
