const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync(__dirname+'/../index.html','utf8');
const app=html.slice(html.indexOf('const CAMEO_DESC'));
const helpers=app.slice(0,app.indexOf('// ---------- base layers'));
const popups=app.slice(app.indexOf('function fmt('),app.indexOf("let mode = 'auto'"));
const scope=vm.createContext({URL,TYPE:{1:{label:'State-based',color:'#000'}},GTYPE:{18:{label:'Assault',color:'#fff'}},GPREC:{1:'country'}});
vm.runInContext(helpers+popups,scope);
const call=(fn,arg)=>{scope.arg=arg;return vm.runInContext(fn+'(arg)',scope)};
test('all inline JavaScript parses',()=>{
 for(const m of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)) if(m[1].trim()) new vm.Script(m[1]);
});
test('source fields are escaped in UCDP popups',()=>{
 const text='<img src=x onerror=alert(1)> & "quoted"';
 const out=call('popupHtml',{t:1,d:'2026-10-24',de:'2026-10-24',ts:Date.UTC(2026,9,24),w:text,s:text,a:text,b:text,h:text,k:1,c:0});
 assert(!out.includes('<img')); assert(out.includes('&lt;img')); assert(out.includes('&amp;'));
});
test('GDELT text and link protocols cannot inject markup',()=>{
 const e={r:18,e:'181',d:'2026-10-24',ts:Date.UTC(2026,9,24),w:'<b>place</b>',a:'<img src=x>',s:'X',n:1,u:'javascript:alert(1)'};
 const out=call('gPopupHtml',e);assert(!out.includes('<a '));assert(!out.includes('<img'));assert(out.includes('&lt;b&gt;place'));
 assert(!call('gTooltipHtml',e).includes('<img'));
 for(const url of ['javascript:alert(1)','data:text/html,test','https://user:password@example.com/']) assert.equal(call('sourceURL',url),'');
 assert.equal(call('sourceURL','https://example.com/story?a=1&b=2'),'https://example.com/story?a=1&b=2');
 const linked=call('gPopupHtml',{...e,u:'https://example.com/story?a=1&b=2'});assert(linked.includes('a=1&amp;b=2'));assert(linked.includes('noopener noreferrer'));
});
test('rebuild defers popup and tooltip formatting until opened',()=>{
 const group=()=>({clearLayers(){this.items=[]},items:[]});const pts=group(),gpts=group();let formats=0;
 const renderer={};const elements={};
 const c=vm.createContext({filtered:[{la:1,lo:2,pla:1,plo:2,k:1,t:1}],gfiltered:[{la:1,lo:2,pla:1,plo:2,n:1,r:18}],heat:{setLatLngs(){}},pts,gpts,incidentRenderer:renderer,TYPE:{1:{color:'#000'}},GTYPE:{18:{color:'#fff'}},document:{getElementById(id){return elements[id]??={}}},popupHtml(){formats++;return 'UCDP'},gPopupHtml(){formats++;return 'GDELT'},gTooltipHtml(){formats++;return 'tooltip'},L:{circleMarker(coords,options){return {options,bindPopup(fn){this.popup=fn;return this},bindTooltip(fn){this.tooltip=fn;return this},addTo(group){group.items.push(this);return this}}}}});
 vm.runInContext(app.slice(app.indexOf('function rebuild()'),app.indexOf('function applyMode()')),c);vm.runInContext('rebuild()',c);
 assert.equal(formats,0);assert.equal(pts.items[0].options.renderer,renderer);assert.equal(gpts.items[0].options.renderer,renderer);
 assert.equal(pts.items[0].popup(),'UCDP');assert.equal(gpts.items[0].popup(),'GDELT');assert.equal(gpts.items[0].tooltip(),'tooltip');assert.equal(formats,3);
});
