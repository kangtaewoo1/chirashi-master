"""Explicitly approved single image-CAPTCHA task; no challenge-page/token bypass."""
import time
import requests


def solve_image(api_key,body,heartbeat=lambda:None,transport=None,timeout=90):
    client=transport or requests.Session()
    def post(method,payload):
        response=client.post('https://api.2captcha.com/'+method,json={'clientKey':api_key,**payload},timeout=(5,15),allow_redirects=False)
        response.raise_for_status();data=response.json()
        if data.get('errorId'):raise RuntimeError('captcha_provider_error')
        return data
    data=post('createTask',{'task':{'type':'ImageToTextTask','body':body,'minLength':1,'maxLength':12}})
    tid=data.get('taskId')
    if not tid:raise RuntimeError('captcha_task_unknown')
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        heartbeat();time.sleep(5)
        data=post('getTaskResult',{'taskId':tid})
        if data.get('status')=='ready':return {'text':data.get('solution',{}).get('text',''),'actual_cost_usd':data.get('cost')}
    raise TimeoutError('captcha_result_unknown_no_recreate')
