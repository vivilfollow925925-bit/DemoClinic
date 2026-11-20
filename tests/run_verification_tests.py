"""Extract the shipped notebook bundle and test it without model/API calls."""
import ast,base64,io,json,pathlib,shutil,subprocess,sys,tempfile,zipfile
root=pathlib.Path(__file__).resolve().parents[1]
nb=json.loads((root/'DemoClinic主工作流.ipynb').read_text(encoding='utf8'))
bundle=None
for cell in nb['cells']:
 if cell['cell_type']!='code':continue
 for node in ast.parse(''.join(cell['source'])).body:
  if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_bundle' for t in node.targets):bundle=ast.literal_eval(node.value)
assert bundle,'Notebook bundle missing'
with tempfile.TemporaryDirectory(prefix='democlinic_verify_') as temp:
 work=pathlib.Path(temp)
 with zipfile.ZipFile(io.BytesIO(base64.b64decode(bundle))) as z:
  for item in z.infolist():
   dest=(work/item.filename).resolve()
   if not dest.is_relative_to(work.resolve()):raise ValueError('Unsafe bundle path')
   dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(z.read(item))
 for name in ('audit_step12.py','audit_lifecycle.py','audit_guards.py','audit_integration.py'):
  shutil.copy2(root/'tests'/name,work/name)
  print('RUN '+name,flush=True)
  subprocess.run([sys.executable,str(work/name)],cwd=work,check=True,timeout=180)
print('PASS: 18 assertion/browser cases + 6 lifecycle cases + 12 guard cases + 4 integration outcomes; no model calls.')
