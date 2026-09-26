import {ref,onMounted,onBeforeUnmount} from 'vue';
import {createReadGate,errorText} from './client.mjs';

// Shared by read-only business views; forms keep their own draft and never poll.
export function useRead(fetch,interval=0){
 const data=ref(null),error=ref(''),busy=ref(false),gate=createReadGate();let timer;
 async function refresh(){const read=gate.begin();busy.value=true;error.value='';try{const value=await fetch(read.signal);if(read.current())data.value=value}catch(e){if(read.current())error.value=errorText(e)}finally{if(read.current())busy.value=false}}
 function poll(){if(document.visibilityState!=='hidden'&&!busy.value)refresh()}
 function visibility(){if(document.visibilityState==='hidden'){gate.begin();busy.value=false}else if(interval)poll()}
 onMounted(()=>{refresh();if(interval)timer=setInterval(poll,interval);document.addEventListener?.('visibilitychange',visibility)});
 onBeforeUnmount(()=>{clearInterval(timer);gate.close();document.removeEventListener?.('visibilitychange',visibility)});
 return {data,error,busy,refresh};
}
