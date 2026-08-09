"use strict";

const chatChoices = Array.from(document.querySelectorAll(".chat-choice"));
const routeStage = document.getElementById("routing-stage");
const routeButton = document.getElementById("route-demo-run");
const routeSourceName = document.getElementById("route-source-name");
const routeSourceMessage = document.getElementById("route-source-message");
const routeSourceId = document.getElementById("route-source-id");
const routeCommand = document.getElementById("route-command");
const routeLive = document.getElementById("route-live");

let selectedRoute = {
  id: "16",
  name: "Family planning",
  message: "Are we still on for six?",
};

function updateRoute(choice) {
  selectedRoute = {
    id: choice.dataset.chatId,
    name: choice.dataset.chatName,
    message: choice.dataset.chatMessage,
  };

  chatChoices.forEach((candidate) => {
    const isSelected = candidate === choice;
    candidate.classList.toggle("is-selected", isSelected);
    candidate.setAttribute("aria-pressed", String(isSelected));
  });

  routeStage.classList.remove("is-routing");
  routeSourceName.textContent = selectedRoute.name;
  routeSourceMessage.textContent = selectedRoute.message;
  routeSourceId.textContent = `chat_id = "${selectedRoute.id}"`;
  routeCommand.textContent = `imsg send --chat-id ${selectedRoute.id}`;
  routeLive.textContent = `Ready to route rowid ${selectedRoute.id}.`;
}

chatChoices.forEach((choice) => {
  choice.addEventListener("click", () => updateRoute(choice));
});

routeButton.addEventListener("click", () => {
  routeStage.classList.remove("is-routing");
  window.requestAnimationFrame(() => {
    routeStage.classList.add("is-routing");
  });
  routeLive.textContent = `Reply routed to exact source rowid ${selectedRoute.id}. No alternate destination was considered.`;
});

const copyStatus = document.getElementById("copy-status");
const copyButtons = Array.from(document.querySelectorAll(".copy-button"));

function restoreCopyButton(button, label) {
  button.textContent = "Copy";
  button.setAttribute("aria-label", label);
}

copyButtons.forEach((button) => {
  const label = button.dataset.copyLabel || "Copy code";
  button.setAttribute("aria-label", label);

  button.addEventListener("click", async () => {
    const target = document.getElementById(button.dataset.copyTarget);
    if (!target || !navigator.clipboard) {
      copyStatus.textContent = "Clipboard access is unavailable. Select the command text to copy it.";
      return;
    }

    try {
      await navigator.clipboard.writeText(target.textContent);
      button.textContent = "Copied";
      button.setAttribute("aria-label", `${label}: copied`);
      copyStatus.textContent = `${label} copied to the clipboard.`;
      window.setTimeout(() => restoreCopyButton(button, label), 1800);
    } catch (_error) {
      copyStatus.textContent = "Copy was blocked by the browser. Select the command text to copy it.";
    }
  });
});
