// script.js

// For custom file inputs: show chosen filename
function showFilename(input, labelId) {
  const labelElem = document.getElementById(labelId);
  if (!labelElem) return;
  if (input.files && input.files[0]) {
    labelElem.textContent = input.files[0].name;
  } else {
    labelElem.textContent = "No file chosen";
  }
}

// For story modal
function openStoryModal(mediaUrl, username) {
  const modal = document.getElementById("storyModal");
  const closeBtn = document.getElementById("closeModal") || document.querySelector(".close");
  const modalMedia = document.getElementById("modalMedia");
  const modalUser = document.getElementById("modalUser");

  modalMedia.innerHTML = "";

  const lowerUrl = mediaUrl.toLowerCase();
  if (lowerUrl.endsWith(".png") || lowerUrl.endsWith(".jpg") ||
      lowerUrl.endsWith(".jpeg") || lowerUrl.endsWith(".gif")) {
    const img = document.createElement("img");
    img.src = mediaUrl;
    modalMedia.appendChild(img);
  } else if (lowerUrl.endsWith(".mp4") || lowerUrl.endsWith(".mov") || lowerUrl.endsWith(".avi")) {
    const vid = document.createElement("video");
    vid.src = mediaUrl;
    vid.controls = true;
    modalMedia.appendChild(vid);
  } else {
    const p = document.createElement("p");
    p.textContent = "Unsupported format.";
    modalMedia.appendChild(p);
  }

  modalUser.textContent = `Story by: ${username}`;
  modal.style.display = "block";

  // Close
  if (closeBtn) {
    closeBtn.onclick = () => {
      modal.style.display = "none";
    };
  }

  // Close if click outside
  window.onclick = (evt) => {
    if (evt.target === modal) {
      modal.style.display = "none";
    }
  };
}
