/* Minimal deterministic DOM fixture runner. This is NOT a real Steam/CEF test.
 * Run validate-steam-ui.py --browser <Chrome> to use the same fixtures in Chrome.
 */
const fs=require('node:fs'),vm=require('node:vm');
function assertPath(){if(!process.argv[2])throw new Error('Pass the generated HTML fixture path');}
assertPath();
class Style {
 constructor(){this.values=new Map();return new Proxy(this,{get:(t,k)=>k in t?(typeof t[k]==='function'?t[k].bind(t):t[k]):t.getPropertyValue(String(k).replace(/[A-Z]/g,m=>'-'+m.toLowerCase())),set:(t,k,v)=>{if(k in t){t[k]=v;}else t.setProperty(k,v);return true;}});}
 setProperty(k,v,p=''){this.values.set(k,[String(v),p]);}
 getPropertyValue(k){return this.values.get(k)?.[0]||'';}
 getPropertyPriority(k){return this.values.get(k)?.[1]||'';}
 removeProperty(k){const old=this.getPropertyValue(k);this.values.delete(k);return old;}
 set cssText(value){this.values.clear();for(const piece of value.split(';')){const i=piece.indexOf(':');if(i<0)continue;let v=piece.slice(i+1).trim(),priority='';if(v.endsWith('!important')){v=v.slice(0,-10).trim();priority='important';}this.setProperty(piece.slice(0,i).trim(),v,priority);}}
 get cssText(){return [...this.values].map(([k,[v,p]])=>k+':'+v+(p?'!'+p:'')).join(';');}
}
function dataName(key){return 'data-'+key.replace(/[A-Z]/g,m=>'-'+m.toLowerCase());}
class Element {
 constructor(tag,owner){this.tagName=tag.toUpperCase();this.ownerDocument=owner;this.attributes=new Map();this.childNodes=[];this.parentNode=null;this.style=new Style();this._text='';this.dataset=new Proxy({},{get:(_,k)=>this.getAttribute(dataName(k))??undefined,set:(_,k,v)=>{this.setAttribute(dataName(k),v);return true;},deleteProperty:(_,k)=>{this.removeAttribute(dataName(k));return true;}});this.classList={contains:c=>this.className.split(/\s+/).includes(c),add:c=>{if(!this.classList.contains(c))this.className=(this.className+' '+c).trim();},remove:c=>{this.className=this.className.split(/\s+/).filter(v=>v!==c).join(' ');}};if(tag==='iframe'){this.contentDocument=new Document();this.contentWindow=this.contentDocument.defaultView;}}
 get id(){return this.getAttribute('id')||'';}set id(v){this.setAttribute('id',v);}
 get className(){return this.getAttribute('class')||'';}set className(v){this.setAttribute('class',v);}
 get parentElement(){return this.parentNode instanceof Element?this.parentNode:null;}
 get children(){return this.childNodes;}get lastChild(){return this.childNodes.at(-1)||null;}
 get nextSibling(){if(!this.parentNode)return null;return this.parentNode.childNodes[this.parentNode.childNodes.indexOf(this)+1]||null;}
 setAttribute(k,v){this.attributes.set(k,String(v));}getAttribute(k){return this.attributes.has(k)?this.attributes.get(k):null;}hasAttribute(k){return this.attributes.has(k);}removeAttribute(k){this.attributes.delete(k);}
 appendChild(el){return this.insertBefore(el,null);}
 insertBefore(el,next){el.remove();el.parentNode=this;let idx=this.childNodes.indexOf(next);if(idx<0)idx=this.childNodes.length;this.childNodes.splice(idx,0,el);return el;}
 remove(){if(this.parentNode){let i=this.parentNode.childNodes.indexOf(this);if(i>=0)this.parentNode.childNodes.splice(i,1);this.parentNode=null;}}
 contains(el){while(el){if(el===this)return true;el=el.parentNode;}return false;}
 matchesOne(selector){
  selector=selector.trim();
  const space=selector.lastIndexOf(' ');if(space>=0&&!selector.includes('[')){let right=selector.slice(space+1);if(!this.matchesOne(right))return false;let p=this.parentNode;while(p){if(p.matchesOne(selector.slice(0,space)))return true;p=p.parentNode;}return false;}
  if(selector.startsWith('#'))return this.id===selector.slice(1);
  if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));
  const attr=selector.match(/^\[([^=*\]]+)(\*=|=)?["']?([^"'\]]*)["']?\]$/);
  if(attr){let v=this.getAttribute(attr[1]);return !attr[2]?v!==null:attr[2]==='='?v===attr[3]:(v||'').includes(attr[3]);}
  return this.tagName.toLowerCase()===selector.toLowerCase();
 }
 matches(selector){return selector.split(',').some(s=>this.matchesOne(s));}
 closest(selector){for(let p=this;p;p=p.parentNode)if(p.matches(selector))return p;return null;}
 querySelectorAll(selector){const out=[];const walk=el=>{for(const child of el.childNodes){if(child.matches(selector))out.push(child);walk(child);}};walk(this);return out;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 get textContent(){return this._text+this.childNodes.map(e=>e.textContent).join('');}
 set textContent(v){this._text=String(v);for(const child of this.childNodes)child.parentNode=null;this.childNodes=[];}
 set innerHTML(v){this.textContent='';// Topbar fixtures require SVG and its trailing label node only.
  if(v.includes('<svg'))this.appendChild(new Element('svg',this.ownerDocument));
  if(v.includes('<span'))this.appendChild(new Element('span',this.ownerDocument));
 }
 cloneNode(deep=false){const e=new Element(this.tagName.toLowerCase(),this.ownerDocument);e.attributes=new Map(this.attributes);e.style.cssText=this.style.cssText;e._text=this._text;if(deep)this.childNodes.forEach(n=>e.appendChild(n.cloneNode(true)));return e;}
}
class Document {
 constructor(){this.documentElement=new Element('html',this);this.head=new Element('head',this);this.body=new Element('body',this);this.documentElement.appendChild(this.head);this.documentElement.appendChild(this.body);this.defaultView={document:this};}
 createElement(tag){return new Element(tag,this);}
 querySelectorAll(selector){const out=this.documentElement.querySelectorAll(selector);if(this.documentElement.matches(selector))out.unshift(this.documentElement);return out;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 getElementById(id){return this.querySelector('#'+id);}
}
const doc=new Document();global.document=doc;global.window=global;doc.defaultView=global;global.parent=global;global.top=global;global.opener=null;
global.requestAnimationFrame=cb=>setTimeout(cb,1);global.cancelAnimationFrame=clearTimeout;
global.MutationObserver=class{observe(){}disconnect(){}};
global.getComputedStyle=el=>({display:el.style.display||'block'});
const results=doc.createElement('pre');results.id='results';results.textContent='RUNNING';doc.body.appendChild(results);
const html=fs.readFileSync(process.argv[2],'utf8');const script=html.match(/<script>([\s\S]*)<\/script>/)[1];
vm.runInThisContext(script,{filename:'steam-ui-fixture.js'});
let rounds=0;const timer=setInterval(()=>{const text=doc.getElementById('results').textContent;if(text.includes('ALL ')||text.includes('FAIL ')){clearInterval(timer);console.log(text);process.exit(text.includes('FAIL ')?1:0);}if(++rounds>100){clearInterval(timer);console.error('Fixture timed out');process.exit(1);}},20);
