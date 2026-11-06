"""Additional retained API and knowledge document workflows against isolated local stores."""
from pathlib import Path
import sys,json,re,uuid,sqlite3
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import ROOT,runtime,load
import httpx
checks=[]
def check(name,value,detail=''):checks.append({'name':name,'passed':bool(value),'detail':detail});assert value,name+' '+detail
def main():
 r=runtime();site=f'http://127.0.0.1:{r["ports"]["site"]}';geo=f'http://127.0.0.1:{r["ports"]["geo"]}';c=httpx.Client(trust_env=False,timeout=30)
 body={'source_route':'/get-a-quote/','source_context':{'source_page':'/get-a-quote/'},'submitted_fields':{'company':'Example RFQ','country':'United States','work_email':'rfq@example.invalid','whatsapp':'+15555550111','expected_quantity':'1000','target_market':'United States','contact_name':'Example Buyer','product_category':load('catalog.json')['collections'][0]['name']['en'],'product_idea':'Isolated demonstration project','formula_route':'Unsure','packaging_requirement':'Confirm suitable options','launch_date':'Project to confirm','consent_inquiry':True,'consent_upload':False}}
 response=c.post(site+'/api/v1/rfqs',json=body,headers={'Origin':site,'Idempotency-Key':str(uuid.uuid4())});check('Full RFQ API retained with configured categories',response.status_code==201,str(response.status_code))
 g=httpx.Client(base_url=geo,trust_env=False,timeout=30);check('Operator login for knowledge workflow',g.post('/login',data={'password':r['admin_password']}).status_code==303)
 page=g.get('/knowledge/new');token=re.search(r'name="csrf" value="([^"]+)"',page.text)[1];category=re.search(r'<option value="([0-9]+)"',page.text)[1];content=(ROOT/'examples/knowledge-note.md').read_bytes()+('\nTest revision '+uuid.uuid4().hex).encode()
 response=g.post('/knowledge/new',data={'csrf':token,'title':'Fictional acceptance document','category_id':category,'summary':'DEMO ONLY','source':'Synthetic repository fixture'},files={'file':('example.md',content,'text/markdown')});check('Knowledge upload extracts and stores source',response.status_code==303)
 id=int(re.search('/knowledge/([0-9]+)',response.headers['location'])[1]);database=ROOT/'.local/data/geo/knowledge-base/knowledge.db'
 def row():
  with sqlite3.connect(database) as db:db.row_factory=sqlite3.Row;return dict(db.execute('SELECT * FROM documents WHERE id=?',(id,)).fetchone())
 check('Knowledge original download matches uploaded bytes',g.get(f'/knowledge/{id}/download').content==content)
 check('Knowledge starts as draft',row()['status']=='draft')
 response=g.post(f'/knowledge/{id}/workflow',data={'csrf':token,'version':row()['version'],'action':'submit'});check('Knowledge submit for review',response.status_code==303 and row()['status']=='pending')
 response=g.post(f'/knowledge/{id}/workflow',data={'csrf':token,'version':row()['version'],'action':'approve','reviewer':'Demo Reviewer','note':'Fictional document accepted for testing only.'});check('Knowledge explicit review approval',response.status_code==303 and row()['status']=='approved')
 response=g.post(f'/knowledge/{id}/workflow',data={'csrf':token,'version':row()['version'],'action':'archive','note':'End of isolated acceptance exercise'});check('Knowledge archive preserves source download',row()['status']=='archived' and g.get(f'/knowledge/{id}/download').content==content)
 check('Knowledge search page and detail remain usable',g.get('/knowledge?status=archived').status_code==200 and g.get(f'/knowledge/{id}').status_code==200)
if __name__=='__main__':
 try:main()
 finally:(ROOT/'reports/workflows.json').write_text(json.dumps(checks,indent=2));print(json.dumps({'passed':sum(x['passed'] for x in checks),'total':len(checks)}))
