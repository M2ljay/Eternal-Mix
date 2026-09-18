/** Check that source and shipped bundle contain the same integration code.
 * Requires the project's TypeScript dev dependency (npm ci).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const ts=require('typescript');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const source=fs.readFileSync(path.join(root,'src/index.tsx'),'utf8');
const bundle=fs.readFileSync(path.join(root,'dist/index.js'),'utf8');
const start=source.indexOf('let fullscreenChromeObservers:');
const end=source.indexOf('function OceanLayer()',start);
const expected=ts.transpileModule(source.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext}}).outputText.replace(/\bRouter\b/g,'DFL.Router').replace(/\bNavigation\b/g,'DFL.Navigation');
const bstart=bundle.indexOf('let fullscreenChromeObservers =');
const bend=bundle.indexOf('function OceanLayer()',bstart);
if([start,end,bstart,bend].some(v=>v<0))throw new Error('Steam integration region missing');
// The parser/printer correctly handles template literal boundaries; a bare scanner
// would need explicit reScanTemplateToken calls and can swallow trailing whitespace.
function normalize(text) {
 const parsed=ts.createSourceFile('integration.js',text,ts.ScriptTarget.ES2020,true,ts.ScriptKind.JS);
 if(parsed.parseDiagnostics.length)throw new Error('Invalid integration JavaScript');
 return ts.createPrinter({removeComments:true,newLine:ts.NewLineKind.LineFeed}).printFile(parsed).trim();
}
if(normalize(expected)!==normalize(bundle.slice(bstart,bend)))throw new Error('Source/bundle Steam integration mismatch');
for(const text of [source,bundle]){
 if(!/onDismount\(\)\s*\{\s*fullscreenUiDisposed\s*=\s*true;\s*forceRestoreFullscreenChrome\(\)/.test(text))throw new Error('Missing dismount cleanup');
}
let files=0;
for(const entry of fs.readdirSync(path.join(root,'src'))){
 if(!/\.tsx?$/.test(entry))continue;
 const result=ts.transpileModule(fs.readFileSync(path.join(root,'src',entry),'utf8'),{fileName:entry,reportDiagnostics:true,compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}});
 const errors=(result.diagnostics||[]).filter(d=>d.category===ts.DiagnosticCategory.Error);
 if(errors.length)throw new Error(entry+': '+errors.map(e=>ts.flattenDiagnosticMessageText(e.messageText,'\n')).join('\n'));
 files++;
}
console.log(`PASS: source/bundle Steam integration parity, dismount cleanup, syntax/transpilation of ${files} TS/TSX files (TypeScript ${ts.version}).`);
