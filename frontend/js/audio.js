// Audio hooks for the browser microphone flow.
export function createRecorder(onAudio) {
  let recorder;
  return {
    async start() {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recorder = new MediaRecorder(stream);
      const chunks = [];
      recorder.ondataavailable = (event) => chunks.push(event.data);
      recorder.onstop = () =>
        onAudio(new Blob(chunks, { type: recorder.mimeType }));
      recorder.start();
    },
    stop() {
      if (recorder?.state === "recording") recorder.stop();
    },
  };
}
