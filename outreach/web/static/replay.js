// The replay page plays a recorded run back. It only adds and removes classes: everything it shows is
// already on the page, so without this script the page simply shows the finished run.
(function () {
  const root = document.getElementById("replay");
  if (!root) return;
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const prompt = document.getElementById("replay-prompt");
  let timers = [];

  const all = (selector) => [...root.querySelectorAll(selector)];
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));

  // Back to start: everything hidden, ready to play.
  function start() {
    timers.forEach(clearTimeout);
    timers = [];
    all(".shown, .settled").forEach((el) => el.classList.remove("shown", "settled"));
    root.classList.add("playing");
  }

  function finish() {
    timers.forEach(clearTimeout);
    timers = [];
    root.classList.remove("playing");
  }

  // asked = someone pressed Play. With "reduce motion" switched on, the page doesn't play by itself,
  // but it still plays when asked to.
  function play(asked) {
    if (still && !asked) return finish();
    start();
    (document.fullscreenElement === root ? root : window).scrollTo({ top: 0, behavior: still ? "auto" : "smooth" });
    let t = 300;
    // 1. The rule steps, one row at a time: the dots arrive, then the ones that stop turn grey.
    all(".rp-step").forEach((step) => {
      later(t, () => step.classList.add("shown"));
      later(t + 700, () => step.classList.add("settled"));
      t += 1400;
    });
    later(t, () => root.querySelector(".rp-result").classList.add("shown"));
    t += 900;
    // 2. One card per doctor, each try in turn. The page scrolls along, so nobody has to while presenting.
    later(t, () => follow(".rp-cards"));
    all(".rp-card").forEach((card) => {
      const tries = [...card.querySelectorAll(".rp-tries li")];
      later(t, () => card.classList.add("shown"));
      tries.forEach((li, i) => later(t + 250 + i * 550, () => li.classList.add("shown")));
      later(t + 250 + tries.length * 550, () => card.classList.add("settled"));
      t += 350 + tries.length * 400;
    });
    // 3. The time bars grow.
    later(t, () => follow(".rp-bars"));
    later(t + 500, () => all(".rp-bar").forEach((bar) => bar.classList.add("shown")));
    later(t + 2200, finish);
  }

  function follow(selector) {
    const section = root.querySelector(selector);
    if (section) section.closest(".rp-section").scrollIntoView({ behavior: still ? "auto" : "smooth", block: "start" });
  }

  function fullScreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (root.requestFullscreen) root.requestFullscreen();
  }

  // After a click, give focus back to the page, so Space plays rather than pressing the same button again.
  const on = (action, fn) => root.querySelector(`[data-action="${action}"]`).addEventListener("click", (event) => {
    event.currentTarget.blur();
    fn();
  });
  on("play", () => play(true));
  on("start", start);
  on("full", fullScreen);
  const promptButton = root.querySelector('[popovertarget="replay-prompt"]');
  if (promptButton) promptButton.addEventListener("click", (event) => event.currentTarget.blur());

  document.addEventListener("keydown", (event) => {
    const typing = event.target instanceof Element && event.target.closest("input, textarea, select, button");
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === " ") {
      event.preventDefault();
      play(true);
    } else if (key === "r") {
      start();
    } else if (key === "p" && prompt) {
      prompt.togglePopover();
    } else if (key === "f") {
      fullScreen();
    }
  });

  play(false);
})();
