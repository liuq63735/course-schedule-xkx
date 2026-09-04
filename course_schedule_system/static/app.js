/* ============================================================
 * 学生组织课程表管理系统 - 前端脚本
 * ============================================================ */
(function () {
  'use strict';

  /* ---------------- 1. 注册页：身份=主席时隐藏部门 ---------------- */
  function initRegister() {
    var identity = document.getElementById('identitySelect');
    var dept = document.getElementById('departmentSelect');
    if (!identity || !dept) return;
    function refresh() {
      var chairman = identity.value === '主席';
      dept.disabled = chairman;
      dept.required = !chairman;
      if (chairman) dept.value = '';
    }
    identity.addEventListener('change', refresh);
    refresh();
  }

  /* ---------------- 2. 课表总览页：按部门过滤成员 + 自动刷新 ---------------- */
  function initSchedulePage() {
    var form = document.getElementById('scheduleFilterForm');
    if (!form) return;
    var dept = document.getElementById('deptFilter');
    var mem = document.getElementById('memberSelect');
    var sem = document.getElementById('semesterSelect');
    var week = document.getElementById('weekInput');

    function memberDeptOf(opt) { return opt.dataset.dept || '无部门'; }

    function applyDept() {
      if (!dept || !mem) return;
      var d = dept.value;
      var shown = 0;
      Array.prototype.forEach.call(mem.options, function (o) {
        var match = (d === 'all') || (memberDeptOf(o) === d);
        // 始终保留当前选中项，避免出现无法提交的情况
        o.hidden = !match && o.value !== mem.value;
        if (!o.hidden) shown++;
      });
      if (shown === 0) mem.title = '该部门暂无成员';
    }

    if (dept) dept.addEventListener('change', applyDept);
    if (mem) mem.addEventListener('change', function () { form.requestSubmit(); });
    if (sem) sem.addEventListener('change', function () { form.requestSubmit(); });
    if (week) week.addEventListener('change', function () {
      var min = parseInt(week.min, 10) || 1;
      var max = parseInt(week.max, 10) || 1;
      var v = parseInt(week.value, 10);
      if (isNaN(v)) v = min;
      if (v < min) v = min;
      if (v > max) v = max;
      week.value = v;
      form.requestSubmit();
    });
    applyDept();
  }

  /* ---------------- 3. 空闲查询页：工作日节次 vs 周末时段 ---------------- */
  function initFreeQuery() {
    var day = document.getElementById('freeDay');
    var pBox = document.getElementById('freePeriodBox');
    var sBox = document.getElementById('freeSlotBox');
    var start = document.getElementById('freeStart');
    var end = document.getElementById('freeEnd');
    if (!day) return;

    function refreshEndOptions() {
      var s = parseInt(start.value, 10);
      Array.prototype.forEach.call(end.options, function (o) {
        o.disabled = parseInt(o.value, 10) < s;
      });
      if (parseInt(end.value, 10) < s) end.value = s;
    }
    function toggle() {
      var weekend = parseInt(day.value, 10) >= 6;
      if (pBox) pBox.style.display = weekend ? 'none' : '';
      if (sBox) sBox.style.display = weekend ? '' : 'none';
    }
    day.addEventListener('change', function () { toggle(); refreshEndOptions(); });
    if (start) start.addEventListener('change', refreshEndOptions);
    refreshEndOptions();
  }

  /* ---------------- 启动 ---------------- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  function boot() {
    initRegister();
    initSchedulePage();
    initFreeQuery();
  }
})();
