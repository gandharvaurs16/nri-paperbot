"""Optional one-way Telegram summary; no incoming bot polling."""
import os,requests,sys

def send(text):
    token=os.getenv('TELEGRAM_BOT_TOKEN');chat=os.getenv('TELEGRAM_CHAT_ID')
    if token and chat:
        r=requests.post('https://api.telegram.org/bot'+token+'/sendMessage',json={'chat_id':chat,'text':text[:3900]},timeout=20);r.raise_for_status()
    else:print('Notification:',text,file=sys.stderr)
if __name__=='__main__':send(' '.join(sys.argv[1:]))
