"use strict";

(() => {
  const data = window.BENCHFORGE_CASES;
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const showcase = window.BENCHFORGE_SHOWCASE?.datasets || [];
  const settings = {
    alfred:{label:"Household",source:"ALFRED",suite:"SpatialHome-Task"},
    habitat:{label:"Navigation",source:"Habitat",suite:"SpatialHome-Nav"},
    carla:{label:"Driving",source:"CARLA",suite:"SpatialDrive"},
    libero:{label:"Manipulation",source:"LIBERO",suite:"SpatialArm"},
    tartanground:{label:"Quadruped",source:"TartanGround",suite:"SpatialQuadruped"},
    uav:{label:"UAV",source:"Aerial imagery",suite:"SpatialUAV"}
  };
  const order = Object.keys(settings);
  let selected = "alfred";
  let sampleIndex = 0;
  let mediaIndex = 0;
  let language = "en";
  let repairId = "sht";
  let repairState = "after";
  let taskIndex = 0;
  const tabs = document.querySelector("#example-tabs");
  const panel = document.querySelector("#example-content");
  const repairPanel = document.querySelector("#repair-content");

  tabs.innerHTML = order.map((id,index) => `<button id="example-tab-${id}" role="tab" aria-controls="example-content" aria-selected="${index === 0}" tabindex="${index === 0 ? 0 : -1}" data-example="${id}"><span>${settings[id].label}</span><small>${settings[id].source} · 10,000</small></button>`).join("");

  function currentDataset() { return showcase.find(item => item.dataset === selected); }
  function currentSample() { return currentDataset()?.examples[sampleIndex]; }

  function renderExample() {
    const dataset = currentDataset();
    if (!dataset?.examples?.length) {
      panel.innerHTML = '<p class="load-error">Example records are being prepared.</p>';
      return;
    }
    const item = currentSample();
    const meta = settings[selected];
    const english = language === "en";
    const image = item.images[mediaIndex] || item.images[0];
    const choices = english ? item.choices_en : item.choices_original;
    const capability = english ? item.capability_en : item.capability;
    const answer = english ? item.answer_display_en : item.answer_display_original;
    document.querySelectorAll("[data-example]").forEach(button => {
      const active = button.dataset.example === selected;
      button.setAttribute("aria-selected",String(active));
      button.tabIndex = active ? 0 : -1;
    });
    panel.setAttribute("aria-labelledby",`example-tab-${selected}`);
    panel.innerHTML = `<div class="example-heading"><h3>${esc(meta.suite)}</h3><p>${dataset.examples.length} selected examples from 10,000 questions</p></div>
      <div class="sample-picker" role="group" aria-label="Select a benchmark example">${dataset.examples.map((sample,index) => `<button data-sample="${index}" aria-pressed="${sampleIndex === index}" class="${sampleIndex === index ? "is-active" : ""}"><img src="${esc(sample.images[0].src)}" alt="" loading="lazy"><span><strong>${String(index+1).padStart(2,"0")}</strong> ${esc(sample.capability_en)}</span></button>`).join("")}</div>
      <div class="showcase-toolbar"><div class="sample-stepper"><button data-step="-1" aria-label="Previous example">←</button><span>Example ${sampleIndex+1} / ${dataset.examples.length}</span><button data-step="1" aria-label="Next example">→</button></div><div class="state-toggle" role="group" aria-label="Question language"><button data-language="en" aria-pressed="${english}" class="${english ? "is-active" : ""}">English translation</button><button data-language="original" aria-pressed="${!english}" class="${!english ? "is-active" : ""}" lang="zh-CN">中文原题</button></div></div>
      <div class="example-main showcase-main"><figure class="example-figure showcase-figure"><a class="figure-link" href="${esc(image.src)}" target="_blank" rel="noopener" aria-label="Open source observation at full size"><img src="${esc(image.src)}" width="${image.width}" height="${image.height}" alt="${esc(item.capability_en)}: ${esc(image.label)}"></a>${item.images.length > 1 ? `<div class="observation-picker" role="group" aria-label="Evidence images">${item.images.map((media,index)=>`<button data-media="${index}" aria-pressed="${index===mediaIndex}" class="${index===mediaIndex ? "is-active" : ""}">${esc(media.label || `Image ${index+1}`)}</button>`).join("")}</div>` : ""}<figcaption>${esc(image.label || "Observation")} · <a href="${esc(image.src)}" target="_blank" rel="noopener">Full-size image</a></figcaption></figure>
      <div class="question-panel" lang="${english ? "en" : "zh-CN"}"><p class="sample-capability">${esc(capability)}</p><p class="question-text">${esc(english ? item.question_en : item.question_original)}</p>${choices?.length ? `<ul class="options">${choices.map(choice=>`<li>${esc(choice)}</li>`).join("")}</ul>` : ""}<details class="reference-answer"><summary>${english ? "Show reference answer" : "查看参考答案"}</summary><p>${esc(answer)}</p></details><p class="record-format"><strong>Format:</strong> ${esc(item.question_type_en || item.question_type)}<span>·</span><strong>Difficulty:</strong> ${esc(item.difficulty)}</p></div></div>
      <p class="dataset-record"><code>${esc(item.id)}</code><button class="record-download" data-download-record>Download this record (JSON)</button></p>`;
  }
  tabs.addEventListener("click",event=>{
    const button=event.target.closest("[data-example]");
    if (!button) return;
    selected=button.dataset.example;
    sampleIndex=0;
    mediaIndex=0;
    renderExample();
  });
  tabs.addEventListener("keydown",event=>{
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const index=event.key==="Home" ? 0 : event.key==="End" ? order.length-1 : (order.indexOf(selected)+(event.key==="ArrowLeft" ? -1 : 1)+order.length)%order.length;
    selected=order[index]; sampleIndex=0; mediaIndex=0;
    renderExample();
    document.querySelector(`#example-tab-${selected}`).focus();
  });
  panel.addEventListener("click",event=>{
    const button=event.target.closest("button");
    if (!button) return;
    let focusSelector;
    if (button.hasAttribute("data-sample")) {
      sampleIndex=Number(button.dataset.sample); mediaIndex=0;
      focusSelector=`[data-sample="${sampleIndex}"]`;
    } else if (button.hasAttribute("data-step")) {
      sampleIndex=(sampleIndex+Number(button.dataset.step)+currentDataset().examples.length)%currentDataset().examples.length; mediaIndex=0;
      focusSelector=`[data-step="${button.dataset.step}"]`;
    } else if (button.hasAttribute("data-language")) {
      language=button.dataset.language;
      focusSelector=`[data-language="${language}"]`;
    } else if (button.hasAttribute("data-media")) {
      mediaIndex=Number(button.dataset.media);
      focusSelector=`[data-media="${mediaIndex}"]`;
    } else if (button.hasAttribute("data-download-record")) {
      const blob=new Blob([JSON.stringify(currentSample(),null,2)],{type:"application/json;charset=utf-8"});
      const url=URL.createObjectURL(blob);
      const link=document.createElement("a");link.href=url;link.download=`${currentSample().id}.json`;link.click();
      setTimeout(()=>URL.revokeObjectURL(url),1000);
      return;
    } else return;
    renderExample();
    panel.querySelector(focusSelector)?.focus({preventScroll:true});
  });

  function renderRepair(id=repairId,nextState=repairState) {
    repairId=id;repairState=nextState;
    const item=data.offline.find(item=>item.id===id);
    const after=repairState==="after",rejected=item.outcome==="REJECTED";
    const question=item.question[after&&!rejected ? "after" : "before"];
    const options=item.options[after&&!rejected ? "after" : "before"] || [];
    repairPanel.innerHTML=`<div class="example-main"><figure class="example-figure"><a href="${esc(item.image)}" target="_blank" rel="noopener"><img src="${esc(item.image)}" alt="${esc(item.embodiment)} repair case" loading="lazy"></a></figure><div class="question-panel"><div class="question-toolbar"><span class="small-label">${rejected&&after ? "Rejected candidate" : "Repair case"}</span><div class="state-toggle" role="group" aria-label="Repair state"><button data-repair-state="before" aria-pressed="${!after}" class="${!after ? "is-active" : ""}">Before</button><button data-repair-state="after" aria-pressed="${after}" class="${after ? "is-active" : ""}">${rejected ? "Decision" : "After repair"}</button></div></div><p class="question-text">${esc(question)}</p><ul class="options">${options.map(option=>`<li>${esc(option)}</li>`).join("")}</ul><p class="answer-line ${!after||rejected ? "before" : ""}">${after&&rejected ? "Rejected: insufficient admissible evidence." : `${after ? "Reference" : "Initial"} answer: <strong>${esc(after ? item.afterAnswer : item.beforeAnswer)}</strong>`}</p></div></div><div class="case-repair"><p><strong>Detected issue.</strong> ${esc(item.failure)}</p><p><strong>Repair.</strong> ${esc(item.repair)}</p><p class="artifact-scope"><strong>Retained:</strong> ${item.retained.map(esc).join("; ")}<br><strong>Rebuilt / revalidated:</strong> ${item.rebuilt.map(esc).join("; ")}</p></div>`;
  }
  document.querySelector("#repair-select").addEventListener("change",event=>renderRepair(event.target.value,"after"));
  repairPanel.addEventListener("click",event=>{
    const button=event.target.closest("[data-repair-state]");
    if (!button) return;
    renderRepair(repairId,button.dataset.repairState);
    repairPanel.querySelector(`[data-repair-state="${repairState}"]`).focus({preventScroll:true});
  });


  function renderTask(index) {
    taskIndex = index;
    const item = data.interactive[index];
    document.querySelectorAll("[data-task]").forEach(button => {
      const active = Number(button.dataset.task) === index;
      button.setAttribute("aria-selected",String(active));
      button.tabIndex = active ? 0 : -1;
    });
    const taskPanel = document.querySelector("#trajectory-content");
    taskPanel.setAttribute("aria-labelledby",index ? "tab-long" : "tab-short");
    taskPanel.innerHTML = `<div class="trajectory-header"><h3>${esc(item.title)}</h3><p class="task-id">${esc(item.taskId)} · ${esc(item.split)}</p></div><figure class="trajectory-figure"><img src="${esc(item.image)}" alt="Recorded observations from the ${item.steps}-step ${esc(item.title.toLowerCase())} trajectory" loading="lazy"></figure><p class="trajectory-caption">Selected observations from a ${item.steps}-step execution. Terminal task success and trajectory-EQA accuracy are measured separately.</p><p class="task-instruction"><strong>Instruction:</strong> ${esc(item.instruction)}</p><p class="trajectory-metrics"><span><strong>${item.steps}</strong> executed steps</span><span><strong>${item.invalidActions}</strong> invalid actions</span><span><strong>${item.success ? "Success" : "Failure"}</strong> terminal outcome</span><span><strong>${esc(item.eqa.display)}</strong> trajectory EQA</span></p><p class="task-download"><a href="${esc(item.example)}" download>Download selected task fields (JSON)</a></p>`;
  }
  document.querySelectorAll("[data-task]").forEach(button => {
    button.addEventListener("click",()=>renderTask(Number(button.dataset.task)));
    button.addEventListener("keydown",event=>{
      if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
      event.preventDefault();
      const index = event.key === "Home" ? 0 : event.key === "End" ? 1 : 1-taskIndex;
      renderTask(index);
      document.querySelector(`[data-task="${index}"]`).focus();
    });
  });

  const goals = [
    {object:"Apple",instruction:"DiningTable",before:"CounterTop"},
    {object:"Potato",instruction:"CounterTop",before:"DiningTable"},
    {object:"Egg",instruction:"CounterTop",before:"DiningTable"},
    {object:"ButterKnife",instruction:"CounterTop",before:"DiningTable"}
  ];
  function renderGoals(after) {
    document.querySelector("#goal-table").innerHTML = `<table class="goal-table ${after ? "is-repaired" : ""}"><thead><tr><th scope="col">Object</th><th scope="col">Instruction destination</th><th scope="col">PDDL destination</th></tr></thead><tbody>${goals.map(goal=>`<tr><td>${goal.object}</td><td>${goal.instruction}</td><td>${after ? goal.instruction : goal.before} ${after ? "✓" : "×"}</td></tr>`).join("")}</tbody></table>`;
    document.querySelector("#goal-verdict").textContent = after ? "The destination assignments agree after repair. Acceptance still requires execution-based revalidation." : "All four destination assignments disagree. Following the instruction cannot satisfy the original hidden goal.";
    ["before","after"].forEach(value=>{
      const button = document.querySelector(`#goal-${value}`);
      const active = (value === "after") === after;
      button.classList.toggle("is-active",active);
      button.setAttribute("aria-pressed",String(active));
    });
  }
  document.querySelector("#goal-before").addEventListener("click",()=>renderGoals(false));
  document.querySelector("#goal-after").addEventListener("click",()=>renderGoals(true));
  renderExample();
  renderRepair();
  renderTask(0);
  renderGoals(false);
})();
