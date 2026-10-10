(function (root) {
  function renderForecast(signal, score, uploadAge, horizon) {
    const sig = signal || {};
    const age = Number(uploadAge);
    const blocked = !signal || uploadAge == null || !Number.isFinite(age) || age >= 5
      || sig.label === '数据延迟';
    if (String(sig.label || '').includes('休市')) {
      return {direction:'WATCH',label:'休市',sub:'非连续交易时段'};
    }
    if (blocked) return {direction:'WATCH',label:'数据延迟',sub:'暂停新判断，等待有效行情'};
    if (sig.direction === 'WATCH') {
      return {direction:'WATCH',label:sig.label || '观望',sub:
        String(sig.label || '').includes('补齐') ? '正在建立'+horizon+'分钟窗口' : '后台暂未给出方向判断'};
    }
    if (!['UP','DOWN'].includes(sig.direction)) {
      return {direction:'WATCH',label:'等待计算',sub:'方向数据暂不可用'};
    }
    const value=Number(score), confidence=Number(sig.confidence_score);
    return {direction:sig.direction,label:sig.label,sub:
      (Number.isFinite(value) ? '方向分 '+(value>=0?'+':'')+Math.round(value)+'/100' : '方向分 --')
      +(Number.isFinite(confidence)&&confidence>0 ? ' · 结构分 '+Math.round(confidence)+'/100' : '')};
  }

  const learningLabels={
    WAITING_DATA:'等待有效样本',WAITING_FRESH_DATA:'等待新行情样本',
    WAITING_NEW_SAMPLES:'等待样本增量',READY_TO_TRAIN:'等待训练时段',
    DATA_SCAN_ERROR:'数据检查失败',TRAINING:'正在训练',ERROR:'训练异常',TRAINED:'训练完成，待验证'
  };
  function learningLabel(training) {
    const state=training?.state || training?.learning_progress?.learning_state;
    return learningLabels[state] || '等待学习状态';
  }
  const api={renderForecast,learningLabel};
  if(typeof module==='object'&&module.exports) module.exports=api;
  root.MonitoringState=api;
})(typeof globalThis==='object'?globalThis:this);
