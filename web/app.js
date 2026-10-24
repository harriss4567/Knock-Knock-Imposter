const AVATAR_IDENTITY = "visitor-avatar";
const VISIT_SECONDS = 180;
const URGENT_SECONDS = 30;
const KNOCK_EVERY_MS = 4500; // knocks again until the visitor's face is at the peephole

const $ = (id) => document.getElementById(id);
const peephole = new Peephole($("view"));
let room = null;
let media = []; // the visitor's attached audio/video elements
let deciding = false;
let deadline = 0;
let clockTimer = null;
let knockTimer = null;
let audio = null;

function status(text) {
  $("status").textContent = text;
}

function show(id, visible) {
  $(id).classList.toggle("hidden", !visible);
}

// A knuckle on a wooden door, synthesized: a low thud under a short click of filtered noise.
function knockOnce(t, loudness) {
  const thud = audio.createOscillator();
  thud.frequency.setValueAtTime(150, t);
  thud.frequency.exponentialRampToValueAtTime(65, t + 0.09);
  const thudGain = audio.createGain();
  thudGain.gain.setValueAtTime(0.0001, t);
  thudGain.gain.exponentialRampToValueAtTime(0.9 * loudness, t + 0.004);
  thudGain.gain.exponentialRampToValueAtTime(0.0001, t + 0.18);
  thud.connect(thudGain).connect(audio.destination);
  thud.start(t);
  thud.stop(t + 0.2);

  const noise = audio.createBuffer(1, audio.sampleRate * 0.06, audio.sampleRate);
  const samples = noise.getChannelData(0);
  for (let i = 0; i < samples.length; i++) samples[i] = Math.random() * 2 - 1;
  const click = audio.createBufferSource();
  click.buffer = noise;
  const band = audio.createBiquadFilter();
  band.type = "bandpass";
  band.frequency.value = 700;
  band.Q.value = 1.2;
  const clickGain = audio.createGain();
  clickGain.gain.setValueAtTime(0.6 * loudness, t);
  clickGain.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
  click.connect(band).connect(clickGain).connect(audio.destination);
  click.start(t);
}

function knock(loudness = 1) {
  audio ??= new AudioContext();
  audio.resume();
  const t = audio.currentTime + 0.05;
  [0, 0.27, 0.54].forEach((dt) => knockOnce(t + dt + Math.random() * 0.03, loudness));
}

function startKnocking() {
  let loudness = 0.8;
  knock(loudness);
  knockTimer = setInterval(() => knock((loudness = Math.min(1.3, loudness + 0.2))), KNOCK_EVERY_MS);
}

function stopKnocking() {
  clearInterval(knockTimer);
  knockTimer = null;
}

function renderClock() {
  const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
  $("clock").textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  $("clock").classList.toggle("urgent", left <= URGENT_SECONDS);
  if (left === 0) decide(false, "The sky is turning grey. Too late to open now.");
}

function startClock() {
  deadline = Date.now() + VISIT_SECONDS * 1000;
  show("clock", true);
  renderClock();
  clockTimer = setInterval(renderClock, 250);
}

function stopClock() {
  clearInterval(clockTimer);
  clockTimer = null;
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `server said ${res.status}`);
  return data;
}

async function startNight() {
  show("intro", false);
  show("ending", false);
  try {
    await post("/api/night");
    await startVisit();
  } catch (err) {
    console.error(err);
    status(`Something went wrong: ${err.message}`);
  }
}

async function startVisit(path = "/api/visit") {
  show("next", false);
  show("reveal", false);
  show("clock", false);
  deciding = false;
  try {
    const { url, token, number, visitors, mouth_shadow } = await post(path);
    peephole.setShadow(mouth_shadow);
    status(`Someone is knocking. (${number} of ${visitors})`);
    startKnocking();

    room = new LivekitClient.Room();
    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, participant) => {
      if (participant.identity !== AVATAR_IDENTITY) return;
      const element = track.attach();
      media.push(element);
      if (track.kind === "video") {
        element.muted = true;
        element.playsInline = true;
        element.className = "source-video"; // kept in the DOM so the browser keeps decoding it
        document.body.appendChild(element);
        peephole.setVideo(element);
        stopKnocking();
        status("");
        if (!deciding) {
          startClock();
          show("open", true);
          show("shut", true);
        }
      } else {
        element.classList.add("hidden");
        document.body.appendChild(element);
      }
    });
    room.on(LivekitClient.RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== "game") return;
      const event = JSON.parse(new TextDecoder().decode(payload));
      if (event.type === "error") {
        stopKnocking();
        status(event.message);
        $("next").textContent = "Knock again";
        $("next").onclick = () => startVisit("/api/visit/retry");
        show("next", true);
      }
      console.log("game event", event);
    });
    room.on(LivekitClient.RoomEvent.Disconnected, () => {
      // Keeps the error and the Knock again button if the session failed.
      if (!deciding && $("next").classList.contains("hidden")) status("The porch is empty.");
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
  } catch (err) {
    stopKnocking();
    console.error(err);
    status(`Something went wrong: ${err.message}`);
  }
}

function revealText(result) {
  const name = result.name;
  if (result.impostor) {
    return result.opened ? `That was not ${name}. And now it is inside.` : `That was not ${name}. The door held.`;
  }
  return result.opened ? `${name} was human. Safe inside.` : `${name} was human. The sun came up.`;
}

async function decide(opened, message) {
  if (deciding) return;
  deciding = true;
  stopClock();
  stopKnocking();
  show("open", false);
  show("shut", false);
  status(message ?? (opened ? "You unhook the chain and open the door." : "You leave the bolt where it is."));
  try {
    await room?.localParticipant.setMicrophoneEnabled(false);
    // Returns once the visitor's last words have played.
    const result = await post("/api/visit/decide", { opened });
    await room?.disconnect();
    room = null;
    media.forEach((element) => element.remove());
    media = [];
    peephole.setVideo(null);
    peephole.flash();
    show("clock", false);
    status("");
    $("reveal").textContent = revealText(result);
    show("reveal", true);
    $("next").textContent = result.finished ? "Wait for the sun" : "Wait for the next knock";
    $("next").onclick = result.finished ? () => showEnding(result) : () => startVisit();
    show("next", true);
  } catch (err) {
    console.error(err);
    status(`Something went wrong: ${err.message}`);
  }
}

function showEnding(result) {
  show("next", false);
  show("reveal", false);
  const inside = `Inside with you at dawn: ${result.humans_in} human${result.humans_in === 1 ? "" : "s"}, `
    + `${result.impostors_in} impostor${result.impostors_in === 1 ? "" : "s"}.`;
  $("ending-title").textContent = result.won ? "You survived the night" : "You did not survive the night";
  $("ending-text").textContent = result.won ? inside : `${inside} You needed more humans than impostors.`;
  show("ending", true);
}

$("start").addEventListener("click", startNight);
$("again").addEventListener("click", startNight);
$("open").addEventListener("click", () => decide(true));
$("shut").addEventListener("click", () => decide(false));
