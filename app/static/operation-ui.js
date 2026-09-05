window.AmigaOperationUI = (() => {
  function create({ panes, api, setLoading, renderPane, modal, setModalAbort, setModalProgress, newUuid }) {
    function abortPresentation(mode) {
      if (mode === "physical") return {
        message: "Stopping Greaseweazle and leaving the drive idle. The disk currently in the drive may be incomplete.",
        details: [
          { label: "Physical disk", value: "Do not rely on it until it has been written and verified again" },
          { label: "Image state", value: "The source image remains unchanged" },
        ],
        error: "Physical write aborted. The source image is unchanged, but the disk in the drive may be incomplete.",
      };
      if (mode === "read-only") return {
        message: "Stopping at the next safe read boundary. No image data is being changed.",
        details: [
          { label: "Safety", value: "The current read or checksum block will finish before stopping" },
          { label: "Image state", value: "The open images remain unchanged" },
        ],
        error: "Read-only operation aborted safely. No image data was changed.",
      };
      if (mode === "atomic") return {
        message: "Stopping at the next safe boundary, then restoring the pre-operation checkpoint.",
        details: [
          { label: "Safety", value: "The current atomic disk command will finish before rollback" },
          { label: "Image state", value: "No partial patch or repair will be retained" },
        ],
        error: "Operation aborted safely. The pre-operation image state was restored.",
      };
      return {
        message: "Finishing the current atomic disk command. No further disks or files will be started.",
        details: [
          { label: "Safety", value: "The current image write will complete or be cleaned up before stopping" },
          { label: "Completed work", value: "Previously completed batch items will be preserved" },
        ],
        error: "Operation aborted safely. Completed items were preserved.",
      };
    }

    async function trackedPaneOperation(index, message, operation, { abortMode = "batch" } = {}) {
      const pane = panes[index];
      const operationId = newUuid();
      let polling = true;
      let abortRequested = false;
      const abort = abortPresentation(abortMode);
      setLoading(index, true, message);
      if (modal.open) {
        setModalAbort(async () => {
          abortRequested = true;
          setModalProgress({
            title: "Stopping operation safely",
            message: abort.message,
            details: abort.details,
          });
          await api(`/api/operations/${operationId}/cancel`, { method: "POST" });
        });
      }
      const poll = async () => {
        try {
          const data = await api(`/api/operations/${operationId}`);
          if (!polling || panes[index] !== pane) return;
          const progress = data.operation;
          if (progress.state === "cancelling") {
            pane.loadingMessage = progress.message;
            if (modal.open) {
              setModalProgress({
                title: "Stopping operation safely",
                message: abort.message,
                details: abort.details,
              });
            }
            renderPane(index);
            return;
          }
          const count = progress.total != null
            ? ` (${progress.current ?? 0} of ${progress.total})`
            : "";
          const nextMessage = `${progress.message}${count}`;
          const displayChanged = (
            pane.loadingMessage !== nextMessage
            || pane.progressCurrent !== progress.current
            || pane.progressTotal !== progress.total
          );
          if (displayChanged) {
            pane.loadingMessage = nextMessage;
            pane.progressCurrent = progress.current;
            pane.progressTotal = progress.total;
            renderPane(index);
          }
          if (modal.open) {
            const elapsed = Number(progress.elapsedSeconds || 0);
            const rate = Number(progress.ratePerSecond || 0);
            const eta = Number(progress.etaSeconds || 0);
            const timing = [];
            if (elapsed >= 1) timing.push({ label: "Elapsed", value: formatDuration(elapsed) });
            if (rate > 0) timing.push({ label: "Throughput", value: `${rate.toFixed(rate >= 10 ? 1 : 2)} work units/second` });
            if (eta > 0) timing.push({ label: "Estimated remaining", value: formatDuration(eta) });
            setModalProgress({
              title: message,
              message: progress.message,
              details: (progress.total != null ? [{
                label: "Progress",
                value: `${Math.round(100 * Number(progress.current || 0) / Number(progress.total || 1))}% complete`,
              }] : []).concat(timing),
            }, progress.current, progress.total);
          }
        } catch (_error) {
          // The first poll can arrive before the POST registers the operation.
        }
      };
      const timer = setInterval(poll, 300);
      try {
        return await operation(operationId);
      } catch (error) {
        if (abortRequested) {
          const aborted = new Error(abort.error);
          aborted.data = error.data;
          throw aborted;
        }
        throw error;
      } finally {
        setModalAbort(null);
        polling = false;
        clearInterval(timer);
        if (panes[index] === pane) {
          pane.loading = false;
          pane.loadingMessage = "";
          pane.progressCurrent = null;
          pane.progressTotal = null;
          renderPane(index);
        }
      }
    }

    function formatDuration(seconds) {
      const value = Math.max(0, Math.round(Number(seconds) || 0));
      if (value < 60) return `${value}s`;
      const minutes = Math.floor(value / 60);
      const remainder = value % 60;
      if (minutes < 60) return `${minutes}m ${remainder}s`;
      return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    }

    async function guardedPaneAction(index, action) {
      const pane = panes[index];
      if (!pane || pane.loading || pane.actionPending) return;
      pane.actionPending = true;
      renderPane(index);
      try {
        await action();
        if (modal.open) {
          await new Promise(resolve => modal.addEventListener("close", resolve, { once: true }));
        }
      } finally {
        if (panes[index] === pane) {
          pane.actionPending = false;
          renderPane(index);
        }
      }
    }

    return { guardedPaneAction, trackedPaneOperation };
  }

  return { create };
})();
