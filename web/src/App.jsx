import { useCallback, useEffect, useState } from "react";

import HeaderRail from "./components/HeaderRail.jsx";
import { ContextRail } from "./components/ContextStrip.jsx";
import ThreadSidebar from "./components/ThreadSidebar.jsx";
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

  const removeThread = useCallback(
    async (id) => {
      await deleteConversation(id).catch(() => {});
      if (id === conversationId) setConversationId(null);
      refreshThreads();
    },
    [conversationId, refreshThreads],
  );

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
        onDelete={removeThread}
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
          navigate={navigate}
          onOpenThreads={() => setDrawerOpen(true)}
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
    </div>
  );
}
