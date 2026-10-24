const AVATAR_IDENTITY = "visitor-avatar";

const $ = (id) => document.getElementById(id);
const peephole = new Peephole($("view"));
let room = null;

function status(text) {
  $("status").textContent = text;
}

async function startVisit() {
  $("intro").classList.add("hidden");
  status("Someone is knocking.");
  try {
    const res = await fetch("/api/visit", { method: "POST" });
    if (!res.ok) throw new Error(`server said ${res.status}`);
    const { url, token } = await res.json();

    room = new LivekitClient.Room();
    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, participant) => {
      if (participant.identity !== AVATAR_IDENTITY) return;
      if (track.kind === "video") {
        const video = track.attach();
        video.muted = true;
        video.playsInline = true;
        video.className = "source-video"; // kept in the DOM so the browser keeps decoding it
        document.body.appendChild(video);
        peephole.setVideo(video);
        status("");
      } else {
        const audio = track.attach();
        audio.classList.add("hidden");
        document.body.appendChild(audio);
      }
    });
    room.on(LivekitClient.RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
      if (topic !== "game") return;
      const event = JSON.parse(new TextDecoder().decode(payload));
      if (event.type === "error") status(event.message);
      console.log("game event", event);
    });
    room.on(LivekitClient.RoomEvent.Disconnected, () => status("The porch is empty."));

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    $("leave").classList.remove("hidden");
  } catch (err) {
    console.error(err);
    status(`Something went wrong: ${err.message}`);
  }
}

async function leaveDoor() {
  $("leave").classList.add("hidden");
  if (room) await room.disconnect();
  await fetch("/api/visit/end", { method: "POST" });
  peephole.setVideo(null);
  status("You step away from the door.");
}

$("start").addEventListener("click", startVisit);
$("leave").addEventListener("click", leaveDoor);
