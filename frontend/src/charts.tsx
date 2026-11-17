import {memo,useEffect,useRef} from 'react';
import {init,use,type EChartsCoreOption,type EChartsType} from 'echarts/core';
import {BarChart,CandlestickChart,LineChart} from 'echarts/charts';
import {GridComponent,TooltipComponent,DataZoomComponent,AxisPointerComponent} from 'echarts/components';
import {CanvasRenderer} from 'echarts/renderers';
import type {Bar,Flow} from './data';

use([BarChart,CandlestickChart,LineChart,GridComponent,TooltipComponent,DataZoomComponent,AxisPointerComponent,CanvasRenderer]);
const red='#ff656f',green='#27d8a8',teal='#49dfc2',line='#222d3a',dim='#8798aa',font='Consolas, monospace';
const tooltip={trigger:'axis',backgroundColor:'#111c29',borderColor:'#344759',textStyle:{color:'#e4edf5',fontFamily:font,fontSize:11},axisPointer:{type:'cross'},confine:true};
const labels={color:dim,fontSize:10,fontFamily:font};
const splitLine={lineStyle:{color:line,type:'dashed'}};
function Canvas({option,label}:{option:EChartsCoreOption;label:string}){
  const root=useRef<HTMLDivElement>(null), chart=useRef<EChartsType|null>(null);
  useEffect(()=>{
    if(!root.current)return;
    const box=root.current,c=init(box);
    chart.current=c;
    const ro=new ResizeObserver(()=>c.resize());ro.observe(box);
    return ()=>{ro.disconnect();c.dispose();chart.current=null};
  },[]);
  useEffect(()=>{chart.current?.setOption(option,{replaceMerge:['series']})},[option]);
  return <div ref={root} className="echart" role="img" aria-label={label}/>;
}

export const PriceChart=memo(function PriceChart({bars,view,period,volumeUnit}:{bars:Bar[];view:'kline'|'line'|'macd'|'rsi';period:string;volumeUnit:string}){
  const minute=period.startsWith('m')&&period!=='month';
  const dates=bars.map(r=>minute?r.t.slice(5,10)+' '+r.t.slice(11,16):r.t.slice(0,10));
  const axis={type:'category',data:dates,axisLine:{lineStyle:{color:line}},axisTick:{show:false},axisLabel:labels,boundaryGap:true};
  const indicators=view==='macd'||view==='rsi';
  const vol=bars.map(r=>({value:r.volume,itemStyle:{color:r.close>=r.open?'#ff656f82':'#27d8a882'}}));
  const price=[{name:'K 线',type:'candlestick',data:bars.map(r=>[r.open,r.close,r.low,r.high]),itemStyle:{color:red,color0:green,borderColor:red,borderColor0:green}},
    {name:'MA5',type:'line',data:bars.map(r=>r.ma5),symbol:'none',lineStyle:{color:'#efb16e',width:1.15},itemStyle:{color:'#efb16e'}},
    {name:'MA20',type:'line',data:bars.map(r=>r.ma20),symbol:'none',lineStyle:{color:'#86a3ff',width:1.15},itemStyle:{color:'#86a3ff'}}];
  const simple={name:'收盘',type:'line',data:bars.map(r=>r.close),symbol:'none',lineStyle:{color:teal,width:1.6},areaStyle:{color:'#29c6ac22'},itemStyle:{color:teal}};
  const top=view==='line'?[simple]:price;
  const bottom=view==='rsi'?[{name:'RSI14',type:'line',data:bars.map(r=>r.rsi),xAxisIndex:1,yAxisIndex:1,symbol:'none',lineStyle:{color:'#a99cff'}}]:
    view==='macd'?[{name:'MACD',type:'bar',data:bars.map(r=>({value:r.macd,itemStyle:{color:(r.macd??0)>=0?red:green}})),xAxisIndex:1,yAxisIndex:1},
      ...(['dif','dea'] as const).map((x,i)=>({name:x.toUpperCase(),type:'line',symbol:'none',data:bars.map(r=>r[x]),xAxisIndex:1,yAxisIndex:1,lineStyle:{color:i?'#86a3ff':'#efb16e',width:1}}))]:
    [{name:'成交量（'+volumeUnit+'）',type:'bar',data:vol,xAxisIndex:1,yAxisIndex:1,barMaxWidth:8}];
  return <Canvas label="价格走势与成交量或技术指标，可滚轮缩放" option={{
    animation:false,tooltip,axisPointer:{link:[{xAxisIndex:'all'}]},
    grid:[{left:12,right:66,top:16,height:'64%'},{left:12,right:66,top:'73%',bottom:34}],
    xAxis:[{...axis,axisLabel:{show:false}},{...axis,gridIndex:1}],
    yAxis:[{scale:true,position:'right',axisLabel:labels,splitLine},{scale:true,position:'right',gridIndex:1,splitNumber:2,
      min:view==='rsi'?0:undefined,max:view==='rsi'?100:undefined,
      axisLabel:indicators?labels:{...labels,formatter:(v:number)=>v>=1e8?(v/1e8).toFixed(1)+'亿':v>=1e4?(v/1e4).toFixed(0)+'万':String(v)},splitLine}],
    dataZoom:[{type:'inside',xAxisIndex:[0,1],zoomOnMouseWheel:true,moveOnMouseMove:true}],
    series:[...top,...bottom],
  }}/>;
});

export const FlowChart=memo(function FlowChart({rows,label}:{rows:Flow[];label:string}){
  return <Canvas label={label} option={{
    animation:false,tooltip,grid:{left:42,right:14,top:16,bottom:25},
    xAxis:{type:'category',data:rows.map(r=>r.t.length===10?r.t.slice(5):r.t.slice(5,10)+' '+r.t.slice(11,16)),axisLabel:labels,axisLine:{lineStyle:{color:line}}},
    yAxis:{type:'value',axisLabel:labels,splitLine},series:[{name:'主力净额（亿元）',type:'line',symbol:'none',data:rows.map(r=>r.main_net==null?null:+(r.main_net/1e8).toFixed(3)),
      lineStyle:{color:teal,width:1.8},itemStyle:{color:teal},areaStyle:{color:'#27d8a821'}}],
  }}/>;
});
