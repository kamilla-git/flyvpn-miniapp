const tg = window.Telegram?.WebApp;

const priceStars = 150;
const goalMeta = {
  youtube: {
    title: "YouTube (4K без лагов)",
    emoji: "📺",
    description: "Стабильный маршрут для видео, стримов и просмотра контента в высоком качестве.",
    countries: ["Германия", "Нидерланды", "Финляндия"],
  },
  social: {
    title: "Instagram / TikTok",
    emoji: "🎬",
    description: "Оптимизированный сценарий для коротких видео, соцсетей и активной мобильной ленты.",
    countries: ["Франция", "Польша", "Швеция"],
  },
  games: {
    title: "Игры и Steam",
    emoji: "🎮",
    description: "Приоритет на маршруты с низким пингом и более ровным распределением нагрузки.",
    countries: ["Финляндия", "Польша", "Германия"],
  },
};

const state = {
  goalKey: "youtube",
  country: "Германия",
};

const goalGrid = document.getElementById("goalGrid");
const countryGrid = document.getElementById("countryGrid");
const summaryTitle = document.getElementById("summaryTitle");
const summaryDescription = document.getElementById("summaryDescription");
const summaryCountry = document.getElementById("summaryCountry");
const priceBadge = document.getElementById("priceBadge");
const buyButton = document.getElementById("buyButton");

function renderGoals() {
  goalGrid.innerHTML = "";

  Object.entries(goalMeta).forEach(([goalKey, goal]) => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `goal-card${goalKey === state.goalKey ? " active" : ""}`;
    card.innerHTML = `
      <div class="goal-emoji">${goal.emoji}</div>
      <div class="goal-title">${goal.title}</div>
      <div class="goal-desc">${goal.description}</div>
    `;
    card.addEventListener("click", () => {
      state.goalKey = goalKey;
      state.country = goal.countries[0];
      render();
    });
    goalGrid.appendChild(card);
  });
}

function renderCountries() {
  countryGrid.innerHTML = "";
  goalMeta[state.goalKey].countries.forEach((country) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `country-chip${country === state.country ? " active" : ""}`;
    chip.textContent = country;
    chip.addEventListener("click", () => {
      state.country = country;
      renderSummary();
      renderCountries();
    });
    countryGrid.appendChild(chip);
  });
}

function renderSummary() {
  const goal = goalMeta[state.goalKey];
  summaryTitle.textContent = goal.title;
  summaryDescription.textContent = goal.description;
  summaryCountry.textContent = state.country;
  priceBadge.textContent = `${priceStars} Stars`;
}

function render() {
  renderGoals();
  renderCountries();
  renderSummary();
}

function buy() {
  const payload = {
    action: "buy",
    goal_key: state.goalKey,
    country: state.country,
  };

  if (tg) {
    buyButton.disabled = true;
    buyButton.textContent = "Отправляю заказ...";
    tg.HapticFeedback?.impactOccurred("medium");
    tg.sendData(JSON.stringify(payload));
    setTimeout(() => tg.close(), 900);
    return;
  }

  alert("Mini App работает внутри Telegram. Откройте эту страницу из бота.");
}

buyButton.addEventListener("click", buy);

if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor("#f6efe4");
  tg.setBackgroundColor("#f6efe4");
}

render();
