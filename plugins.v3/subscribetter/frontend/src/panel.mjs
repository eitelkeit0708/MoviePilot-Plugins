import {inject,nextTick} from 'vue';

// Both business details use the same scrolling surface as their list.
export function usePanelPosition(){
 const panel=inject('subscribetter:scroll',null);let position=0,trigger=null;
 return {
  remember(event){position=panel?.value?.scrollTop||0;trigger=event?.currentTarget||null},
  async top(){await nextTick();if(panel?.value)panel.value.scrollTop=0},
  async restore(){await nextTick();if(panel?.value)panel.value.scrollTop=position;trigger?.focus?.({preventScroll:true})}
 };
}
