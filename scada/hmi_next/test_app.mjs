import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('./app.js',import.meta.url),'utf8');
const init=source.indexOf("\n$('operator-toggle').addEventListener");
assert.ok(init>0,'expected startup boundary');
const context=vm.createContext({document:{getElementById:()=>null},Date,Map,Number,Object,Array,Math});
vm.runInContext(source.slice(0,init)+'\nglobalThis.testAPI={decodeRows,drawPackages,packages,setData:(d)=>{lastData=d;}};',context);
const {decodeRows,drawPackages,packages,setData}=context.testAPI;

function sample(status=1,zoneQuality=1){return {
  plant_mode:1,connected:true,accumulation_mode:1,photoeye_mode:1,
  plant_fault:0,zone_age:0,plant_status:[status,0,0],plant_lane:[1,0,0],plant_age:[0,0,0],
  plant_rows:[[36,0,3,4,4,1,136,2,0,0],[],[]],
  zone_rows:[[3,2,5,45,4,zoneQuality],[],[]],
  photoeye_rows:[[36,0,3,4,4,4,4,0],[],[]],
};}

test('package identity, held zone and position come from one PLC row',()=>{
  const rows=decodeRows(sample());
  const row=rows.get('l1-36-3-4-4');
  assert.equal(rows.size,1);
  assert.equal(row.pos,13.6);
  assert.equal(row.hold,true);
  assert.equal(row.stale,false);
  assert.equal(row.zone[2],5);
});

test('invalid zone quality and PLC slot status mark a package stale',()=>{
  assert.equal([...decodeRows(sample(1,0)).values()][0].stale,true);
  assert.equal([...decodeRows(sample(3,1)).values()][0].stale,true);
});

test('an occupied slot with missing identity keeps its last displayed position',()=>{
  const removed=[];const classes=[];
  packages.set('l1-36-3-4-4',{last:{slot:1,pos:13.6},group:{remove:()=>removed.push(true),classList:{add:v=>classes.push(v)}}});
  setData({plant_status:[3,0,0]});drawPackages(new Map(),true);
  assert.equal(packages.size,1);
  assert.deepEqual(classes,['stale']);
  assert.equal(removed.length,0);
  setData({plant_status:[0,0,0]});drawPackages(new Map(),true);
  assert.equal(packages.size,0);
  assert.equal(removed.length,1);
});

test('a stale row never moves a previously displayed parcel',()=>{
  const moved=[];
  const id='l1-36-3-4-4';
  packages.set(id,{last:{slot:1,pos:13.6},group:{setAttribute:(...args)=>moved.push(args),classList:{toggle:()=>{}},remove:()=>{}}});
  const d=sample(3);
  d.plant_rows[0][6]=182; // raw position changed after loss of freshness
  setData(d);drawPackages(decodeRows(d),true);
  assert.equal(moved.length,0);
  assert.equal(packages.get(id).last.pos,13.6);
  packages.clear();
});
