import {ref,onBeforeUnmount} from 'vue';
import {createReadGate,errorText,readFollowup,clearFollowup} from './client.mjs';
export function useStatus(client) {
  const current=ref(null),health=ref(null),receipt=ref(null),loading=ref(false),error=ref(''),lastSuccess=ref('');
  const saveState=ref('');
  const gate=createReadGate();onBeforeUnmount(()=>gate.close());
  async function refresh(){
    const read=gate.begin();loading.value=true;error.value='';saveState.value='';
    try {
      const [c,h]=await Promise.all([client.get('/configuration',{signal:read.signal}),client.get('/diagnostics',{signal:read.signal})]);
      if(!read.current())return;
      if(h.snapshot?.config_revision!==undefined&&h.snapshot.config_revision!==c.revision)throw Error('STALE_READ_REFRESH_REQUIRED');
      current.value=c;health.value=h;receipt.value=null;lastSuccess.value=new Date().toISOString();
      const reference=c.config?.configuration_receipt;
      if(reference){const r=await client.get('/migration/receipts/'+encodeURIComponent(reference),{signal:read.signal});if(read.current())receipt.value=r;}
      if(read.current()){
        const pending=readFollowup(client.pluginId);
        if(pending?.receipt){
          if(c.config.configuration_receipt===pending.receipt&&(!pending.digest||c.digest===pending.digest)&&receipt.value?.state==='APPLIED'){saveState.value='设置已保存并确认生效';clearFollowup(client.pluginId)}
          else saveState.value='上次设置提交尚未确认生效，请刷新核对；当前展示的是实际运行配置。';
        }
      }
      return read.current()?c:null;
    } catch(e){if(read.current())error.value=errorText(e);}
    finally{if(read.current())loading.value=false;}
  }
  return {current,health,receipt,loading,error,lastSuccess,saveState,refresh};
}
