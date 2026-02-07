fetch("/admin/api/top10")
  .then(r => r.json())
  .then(d => {
    let html = "<tr><th>昵称</th><th>UIN</th><th>次数</th></tr>";
    d.top10.forEach(x => {
      html += `<tr><td>${x.name}</td><td>${x.uin}</td><td>${x.visits}</td></tr>`;
    });
    document.getElementById("top10").innerHTML = html;
  });

fetch("/admin/api/unique_total")
  .then(r => r.json())
  .then(d => {
    document.getElementById("unique").innerText =
      d.unique_users_total;
  });

function queryUin() {
  let u = document.getElementById("uin").value;
  fetch("/admin/api/uin/" + u)
    .then(r => r.json())
    .then(d => {
      let html = "<tr><th>时间</th><th>说说ID</th></tr>";
      d.records.forEach(r => {
        html += `<tr><td>${r.time_human}</td><td>${r.shuoshuo_id || ""}</td></tr>`;
      });
      document.getElementById("records").innerHTML = html;
    });
}
