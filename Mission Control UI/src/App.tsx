import { useEffect } from "react";
import { connectEvents } from "./api/client";
import DetailPanel from "./components/DetailPanel";
import KanbanBoard from "./components/KanbanBoard";
import TopBar from "./components/TopBar";
import { useStore } from "./store";

export default function App() {
  const { refreshAll, handleEvent, setConn, apiOk, selectMission } = useStore();

  useEffect(() => {
    void refreshAll();
    const poll = window.setInterval(() => void refreshAll(), 10000); // safety net behind the websocket
    const stop = connectEvents({ onEvent: handleEvent, onState: setConn });
    return () => { window.clearInterval(poll); stop(); };
  }, [refreshAll, handleEvent, setConn]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") selectMission(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectMission]);

  return (
    <div className="h-full flex flex-col">
      <TopBar />
      {!apiOk && (
        <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/20 text-xs text-red-300 text-center">
          Can't reach the backend — showing the last data received. Retrying automatically.
        </div>
      )}
      <KanbanBoard />
      <DetailPanel />
    </div>
  );
}
