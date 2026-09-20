import {ref,onBeforeUnmount} from 'vue';
import {createReadGate,errorText} from './client.mjs';
export function useStatus(client) {
  const current=ref(null),health=ref(null),receipt=ref(null),loading=ref(false),error=ref('');
  const gate=createReadGate();onBeforeUnmount(()=>gate.close());
  async function refresh(){
    const read=gate.begin();loading.value=true;error.value='';
    try {
      const [c,h]=await Promise.all([client.get('/configuration',{signal:read.signal}),client.get('/diagnostics',{signal:read.signal})]);
      if(!read.current())return;
      if(h.snapshot?.config_revision!==undefined&&h.snapshot.config_revision!==c.revision)throw Error('STALE_READ_REFRESH_REQUIRED');
      current.value=c;health.value=h;receipt.value=null;
      const reference=c.config?.configuration_receipt;
      if(reference){const r=await client.get('/migration/receipts/'+encodeURIComponent(reference),{signal:read.signal});if(read.current())receipt.value=r;}
      return read.current()?c:null;
    } catch(e){if(read.current())error.value=errorText(e);}
    finally{if(read.current())loading.value=false;}
  }
  return {current,health,receipt,loading,error,refresh};
}
