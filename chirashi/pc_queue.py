"""PC executor for leased, reviewed real queue jobs. No signup or challenge solver."""
import json,time,os
from pathlib import Path
import requests

def run(once=False,idle=10):
    import pc_node as settings
    import app
    base=Path(__file__).resolve().parent/'data'
    outbox=base/'pc_queue_outbox.json'
    headers={'User-Agent':'chirashi-pc-queue/1'}
    def api(action,payload):
        response=requests.post(settings.SERVER+'/api/pipeline/pc-queue/'+action,
            params={'token':settings.SERVER_TOKEN},json=dict(payload,node_id=settings.NODE_ID),headers=headers,timeout=60)
        response.raise_for_status()
        result=response.json()
        if not result.get('ok'): raise RuntimeError('PC queue request rejected')
        return result
    def save_report(report):
        temp=outbox.with_suffix('.tmp');temp.write_text(json.dumps(report,ensure_ascii=False),encoding='utf-8');os.replace(temp,outbox)
    # Keep PC settings read-only; engine status writes belong to the server queue.
    app.load_config=lambda:json.loads((base/'config.json').read_text(encoding='utf-8'))
    app.set_site_flag=lambda *a,**k:None
    app.history_update=lambda *a,**k:None
    settings.log('PC 실제 큐 모드 시작 — 보안문자 제외, 가입 및 별도 재발행 없음')
    while True:
        try:
            if outbox.exists():
                api('report',json.loads(outbox.read_text(encoding='utf-8')))
                outbox.unlink()
                settings.log('PC 큐 결과 서버 반영 완료')
                if once:return
            job=api('claim',{'protocol':1}).get('job')
            if not job:
                if once:return
                time.sleep(max(3,idle));continue
            auth={'job_id':job['job_id'],'lease_token':job['lease_token']}
            api('start',auth) # Durable submit marker precedes any external publication.
            site=dict(job['site'],_maintenance_job_id=job['job_id'])
            try:
                ok,result=app.do_post(site,job['title'],job['content'])
                report=dict(auth,ok=ok is True,result_url=str(result) if ok else '',message='' if ok else str(result)[:200])
            except Exception:
                report=dict(auth,ok=False,result_url='',message='PC 실행 예외 — 제출 여부 확인 전 재실행 금지')
            save_report(report)
            settings.log('PC 큐 작업 처리 완료; 결과 검증 회신 대기')
        except KeyboardInterrupt:
            return
        except Exception as exc:
            settings.log('PC 큐 통신 대기: '+type(exc).__name__)
            if once:return
            time.sleep(max(5,idle))

if __name__=='__main__':run()
