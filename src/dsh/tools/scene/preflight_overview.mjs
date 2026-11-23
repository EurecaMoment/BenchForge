// Summarize only the default view. Pointers always address the unmodified report.
export function preflightOverview(report, view, budget) {
  const issues=report.issues, blocking=[];
  const result={schema:report.schema,passed:report.passed,status:report.status,summary:report.summary,
    report_path:report.report_path,program_snapshot_path:report.program_snapshot_path,
    issues:{total:issues.length,json_pointer:'/issues'},blocking_total:0,blocking,
    report_view:view};
  for(const [index,issue] of issues.entries()) {
    if(!issue || !(issue.blocking===true || issue.severity==='error'))continue;
    result.blocking_total++;
    if(result.next_blocking_pointer)continue;
    const pointer='/issues/'+index;
    blocking.push({code:issue.code,path:issue.path,message:String(issue.message??'').slice(0,320),json_pointer:pointer});
    if(JSON.stringify(result).length+100>budget) {
      blocking.pop(); result.next_blocking_pointer=pointer;
    }
  }
  return result;
}
