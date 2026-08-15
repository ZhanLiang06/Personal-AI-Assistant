import { useCallback, useEffect, useState } from "react";

import HeaderRail from "./components/HeaderRail.jsx";
import { ContextRail } from "./components/ContextStrip.jsx";
import ThreadSidebar from "./components/ThreadSidebar.jsx";
import ConfirmDialog from "./components/ConfirmDialog.jsx";
import { useToast } from "./components/Toast.jsx";
import ChatPage from "./pages/ChatPage.jsx";
import FinancePage from "./pages/FinancePage.jsx";
import { deleteConversation, listConversations } from "./lib/api.js";
import { useRoute } from "./lib/router.js";
import { useContextData } from "./lib/useContextData.js";
import { useTheme } from "./lib/useTheme.js";

export default function App() {
  const { theme, mode, setTheme, toggleMode } = useTheme();
  const [path, navigate] = useRoute();

  const [threads, setThreads] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pendingThread, setPendingThread] = useState(null);

  const toast = useToast();

  // The landing tiles and the retracted header rail read the same snapshot.
  const context = useContextData();

  const refreshThreads = useCallback(() => {
    listConversations()
      .then(setThreads)
      .catch(() => setThreads([]));
  }, []);

  useEffect(refreshThreads, [refreshThreads]);

  const openThread = useCallback(
    (id) => {
      setConversationId(id);
      setDrawerOpen(false);
      navigate("/");
    },
    [navigate],
  );

  const newThread = useCallback(() => {
    setConversationId(null);
    setDrawerOpen(false);
    navigate("/");
  }, [navigate]);

  // Threads are hard-deleted: the events go with them, so the agent could not
  // rebuild the conversation even if we kept the row. The dialog says so, and
  // the toast that follows offers no undo, because there is none to offer.
  const askRemoveThread = useCallback((thread) => setPendingThread(thread), []);

  const removeThread = useCallback(async () => {
    const thread = pendingThread;
    if (!thread) return;
    setPendingThread(null);

    try {
      await deleteConversation(thread.id);
      if (thread.id === conversationId) setConversationId(null);
      toast.show({ message: `Deleted "${thread.title || "Untitled thread"}".`, tone: "warn" });
    } catch (failure) {
      toast.show({ message: `Could not delete the thread: ${failure.message}`, tone: "bad" });
    } finally {
      refreshThreads();
    }
  }, [pendingThread, conversationId, refreshThreads, toast]);

  const onFinance = path === "/finance";

  return (
    <div className="flex h-full">
      <div className="atmosphere" aria-hidden="true" />

      <ThreadSidebar
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        threads={threads}
        activeId={conversationId}
        onOpen={openThread}
        onDelete={askRemoveThread}
        onNew={newThread}
        navigate={navigate}
        path={path}
        theme={theme}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <HeaderRail
          theme={theme}
          mode={mode}
          setTheme={setTheme}
          toggleMode={toggleMode}
          path={path}
          navigate={navigate}
          onOpenThreads={() => setDrawerOpen(true)}
          onNewThread={newThread}
          rail={
            !onFinance && conversationId ? (
              <ContextRail today={context.today} overview={context.overview} />
            ) : null
          }
        />

        {onFinance ? (
          <FinancePage theme={theme} />
        ) : (
          <ChatPage
            theme={theme}
            navigate={navigate}
            conversationId={conversationId}
            setConversationId={setConversationId}
            onThreadsChanged={refreshThreads}
            context={context}
          />
        )}
      </div>

      <ConfirmDialog
        open={pendingThread !== null}
        title="Delete this thread?"
        detail={`"${pendingThread?.title || "Untitled thread"}" and everything in it goes for good. This cannot be undone.`}
        confirmLabel="delete"
        onConfirm={removeThread}
        onCancel={() => setPendingThread(null)}
      />
    </div>
  );
}
