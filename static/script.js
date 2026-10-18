let movies = [];
let selectedIndex = null;
let isSearching = false;
let searchResults = [];

const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const movieCount = document.getElementById('movieCount');
const loadingContainer = document.getElementById('loadingContainer');
const movieListContainer = document.getElementById('movieListContainer');
const movieGrid = document.getElementById('movieGrid');
const movieTotal = document.getElementById('movieTotal');
const modal = document.getElementById('movieModal');
const modalTitle = document.getElementById('modalTitle');
const modalBody = document.getElementById('modalBody');

const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const searchResultsContainer = document.getElementById('searchResultsContainer');
const searchResultsGrid = document.getElementById('searchResultsGrid');
const searchStatus = document.getElementById('searchStatus');
const searchLoading = document.getElementById('searchLoading');

document.addEventListener('DOMContentLoaded', () => {
    setupSearch();
    refreshFromDatabase();
    // Refresh the visible list regularly; server-side fetching runs independently.
    window.setInterval(refreshFromDatabase, 10000);
});

function setupSearch() {
    if (searchBtn) searchBtn.addEventListener('click', performSearch);
    if (searchInput) {
        searchInput.addEventListener('keypress', (event) => {
            if (event.key === 'Enter') performSearch();
        });
    }
}

async function performSearch() {
    const query = searchInput?.value.trim();
    if (!query) {
        setStatus('error', 'Please enter a search query');
        return;
    }
    if (isSearching) return;
    isSearching = true;
    if (searchBtn) {
        searchBtn.disabled = true;
        searchBtn.textContent = '⏳ Searching...';
    }
    if (searchLoading) searchLoading.style.display = 'block';
    if (searchResultsContainer) searchResultsContainer.style.display = 'block';
    if (searchStatus) searchStatus.textContent = 'Searching in saved movies: ' + query;

    try {
        // Search the locally stored database-backed movie list.
        const response = await fetch('/api/movies');
        const data = await response.json();
        const q = query.toLowerCase();
        searchResults = (data.movies || []).filter(title => title.toLowerCase().includes(q));
        displaySearchResults(searchResults);
        if (searchStatus) searchStatus.textContent = `Found ${searchResults.length} saved movies`;
        if (searchResults.length) setStatus('success', `Found ${searchResults.length} movies`);
        else setStatus('success', 'No matching saved movies');
    } catch (error) {
        console.error('Search error:', error);
        setStatus('error', 'Could not search saved movies');
    } finally {
        isSearching = false;
        if (searchBtn) {
            searchBtn.disabled = false;
            searchBtn.textContent = '🔍 Search';
        }
        if (searchLoading) searchLoading.style.display = 'none';
    }
}

function displaySearchResults(results) {
    if (!searchResultsContainer || !searchResultsGrid) return;
    searchResultsContainer.style.display = 'block';
    searchResultsGrid.innerHTML = results.map(title => {
        const index = movies.indexOf(title);
        return `<div class="movie-card search-result" onclick="getMovieDetails(${index})">
            <span class="card-index">🔍</span>
            <span class="card-title">${escapeHtml(title)}</span>
            <span class="card-arrow">→</span>
        </div>`;
    }).join('');
}

async function refreshFromDatabase() {
    try {
        const [moviesResponse, statusResponse] = await Promise.all([
            fetch('/api/movies', { cache: 'no-store' }),
            fetch('/api/status', { cache: 'no-store' })
        ]);
        if (!moviesResponse.ok || !statusResponse.ok) throw new Error('Server returned an error');
        const movieData = await moviesResponse.json();
        const state = await statusResponse.json();
        movies = movieData.movies || [];
        updateMovieCount(movies.length);
        displayMovies(movies);

        if (state.is_loading) {
            setStatus('loading', 'Checking for new movies...');
            if (loadingContainer) loadingContainer.style.display = 'block';
        } else {
            if (loadingContainer) loadingContainer.style.display = 'none';
            if (state.last_error) {
                setStatus('error', 'Fetch issue; will retry automatically');
            } else {
                const time = state.last_update ? new Date(state.last_update).toLocaleTimeString() : 'waiting for first check';
                setStatus('success', `Automatic checking enabled · Last check: ${time}`);
            }
        }
    } catch (error) {
        console.error('Could not refresh movie list:', error);
        setStatus('error', 'Connecting to server...');
    }
}

async function loadMovies() {
    await refreshFromDatabase();
}

async function getMovieDetails(index) {
    if (!Number.isInteger(index) || index < 0) {
        setStatus('error', 'Movie is not in the saved list');
        return;
    }
    selectedIndex = index;
    await openModal(index);
}

async function openModal(index) {
    try {
        const response = await fetch(`/api/movie/${index}`);
        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || 'Movie not found');
        modalTitle.textContent = data.title || 'Movie Details';
        renderDetails(data.details || []);
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    } catch (error) {
        modalTitle.textContent = 'Error';
        modalBody.innerHTML = `<p class="detail-error">${escapeHtml(error.message)}</p>`;
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal() {
    modal.classList.remove('active');
    document.body.style.overflow = 'auto';
}

function setStatus(type, text) {
    if (statusDot) {
        statusDot.className = 'status-dot';
        if (type === 'loading') statusDot.classList.add('loading');
        if (type === 'error') statusDot.classList.add('error');
    }
    if (statusText) statusText.textContent = text;
}

function updateMovieCount(count) {
    if (movieCount) movieCount.textContent = `${count} movies stored`;
}

function displayMovies(titles) {
    if (!movieListContainer || !movieGrid || !movieTotal) return;
    if (!titles || titles.length === 0) {
        movieListContainer.style.display = 'none';
        return;
    }
    movieListContainer.style.display = 'block';
    movieTotal.textContent = `${titles.length} movies`;
    movieGrid.innerHTML = titles.map((title, index) => `
        <div class="movie-card" onclick="getMovieDetails(${index})">
            <span class="card-index">#${index + 1}</span>
            <span class="card-title">${escapeHtml(title)}</span>
            <span class="card-arrow">→</span>
        </div>
    `).join('');
}

function renderDetails(details) {
    if (!details || details.length === 0) {
        modalBody.innerHTML = '<p class="detail-empty">No details available</p>';
        return;
    }
    modalBody.innerHTML = details.map(detail => {
        const type = detail.type || 'none';
        const badgeText = type === 'magnet' ? '🧲 Magnet' : (type === 'error' ? '❌ Error' : '⚠️ No Link');
        const screenshotUrls = detail.screenshots || (detail.screenshot ? [detail.screenshot] : []);
        const screenshotsHtml = screenshotUrls.length ? `
            <div class="detail-screenshots">${screenshotUrls.map(src =>
                `<img src="${escapeHtml(src)}" alt="Movie screenshot" onerror="this.style.display='none'">`
            ).join('')}</div>` : '';
        const magnetHtml = detail.magnet ? `
            <div class="detail-magnet">${escapeHtml(detail.magnet)}</div>
            <button class="detail-copy-btn" data-magnet="${escapeAttribute(detail.magnet)}">📋 Copy Magnet</button>` : '';
        return `<div class="detail-item">
            <div class="detail-title">🎬 ${escapeHtml(detail.title || '')}</div>
            <div class="detail-type-badge ${type}">${badgeText}</div>
            ${detail.quality ? `<div class="detail-quality">📺 Quality: ${escapeHtml(detail.quality)}</div>` : ''}
            ${screenshotsHtml}
            ${magnetHtml}
            ${detail.message ? `<p class="${type === 'error' ? 'detail-error' : 'detail-empty'}">${escapeHtml(detail.message)}</p>` : ''}
        </div>`;
    }).join('');
}

function escapeAttribute(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/\"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

if (modalBody) {
    modalBody.addEventListener('click', event => {
        const button = event.target.closest('.detail-copy-btn');
        if (button) copyToClipboard(button, button.getAttribute('data-magnet') || '');
    });
}

async function copyToClipboard(button, text) {
    try {
        await navigator.clipboard.writeText(text);
        button.textContent = '✅ Copied!';
        window.setTimeout(() => button.textContent = '📋 Copy Magnet', 2000);
    } catch (error) {
        console.error('Clipboard copy failed:', error);
        setStatus('error', 'Could not copy to clipboard');
    }
}

function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
}

document.addEventListener('keydown', event => {
    if (event.key === 'Escape') closeModal();
});
if (modal) {
    modal.addEventListener('click', event => {
        if (event.target === modal) closeModal();
    });
}
