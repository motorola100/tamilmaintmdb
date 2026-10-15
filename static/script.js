// State
let movies = [];
let isFetching = false;
let selectedIndex = null;
let isSearching = false;
let searchResults = [];

// DOM Elements
const startBtn = document.getElementById('startBtn');
const viewBtn = document.getElementById('viewBtn');
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

// Search Elements
const searchContainer = document.getElementById('searchContainer');
const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const searchResultsContainer = document.getElementById('searchResultsContainer');
const searchResultsGrid = document.getElementById('searchResultsGrid');
const searchStatus = document.getElementById('searchStatus');
const searchLoading = document.getElementById('searchLoading');

// ============================================================
// INITIALIZATION
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
    checkStatus();
    setupSearch();
});

// ============================================================
// SEARCH SETUP
// ============================================================

function setupSearch() {
    if (searchBtn) {
        searchBtn.addEventListener('click', performSearch);
    }
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                performSearch();
            }
        });
    }
}

async function performSearch() {
    const query = searchInput.value.trim();
    if (!query) {
        setStatus('error', 'Please enter a search query');
        return;
    }

    if (isSearching) return;

    isSearching = true;
    searchBtn.disabled = true;
    searchBtn.textContent = '⏳ Searching...';
    searchLoading.style.display = 'block';
    searchResultsContainer.style.display = 'none';
    searchStatus.textContent = 'Searching for: ' + query;
    searchStatus.style.display = 'block';

    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();

        if (data.status === 'searching') {
            // Poll for search results
            await pollSearchResults();
        } else if (data.status === 'error') {
            setStatus('error', data.message);
        }
    } catch (error) {
        console.error('Search error:', error);
        setStatus('error', 'Network error occurred');
    } finally {
        isSearching = false;
        searchBtn.disabled = false;
        searchBtn.textContent = '🔍 Search';
        searchLoading.style.display = 'none';
    }
}

async function pollSearchResults() {
    let attempts = 0;
    const maxAttempts = 30; // 30 seconds timeout

    while (attempts < maxAttempts) {
        try {
            const response = await fetch('/api/search/status');
            const data = await response.json();

            if (!data.is_searching) {
                // Search complete
                searchResults = data.results || [];
                if (searchResults.length > 0) {
                    displaySearchResults(searchResults);
                    searchStatus.textContent = `Found ${searchResults.length} results`;
                    setStatus('success', `Found ${searchResults.length} movies`);
                } else {
                    searchStatus.textContent = 'No results found';
                    setStatus('error', 'No movies found');
                }
                return;
            }

            // Wait 1 second before next poll
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
        } catch (error) {
            console.error('Polling error:', error);
            break;
        }
    }

    // Timeout
    searchStatus.textContent = 'Search timeout';
    setStatus('error', 'Search timed out');
}

function displaySearchResults(results) {
    searchResultsContainer.style.display = 'block';
    searchResultsGrid.innerHTML = results.map((title, index) => {
        // Use negative index to differentiate from main list
        const realIndex = -(index + 1);
        return `
            <div class="movie-card search-result" onclick="getMovieDetails(${realIndex})">
                <span class="card-index">🔍 #${index + 1}</span>
                <span class="card-title">${escapeHtml(title)}</span>
                <span class="card-arrow">→</span>
            </div>
        `;
    }).join('');
}

// ============================================================
// API CALLS
// ============================================================

async function fetchMovies() {
    if (isFetching) return;
    
    isFetching = true;
    startBtn.disabled = true;
    viewBtn.disabled = true;
    
    setStatus('loading', 'Fetching movies...');
    loadingContainer.style.display = 'block';
    movieListContainer.style.display = 'none';
    if (searchResultsContainer) {
        searchResultsContainer.style.display = 'none';
    }
    
    try {
        const response = await fetch('/api/start');
        const data = await response.json();
        
        if (data.status === 'success') {
            movies = data.movies;
            updateMovieCount(movies.length);
            viewBtn.disabled = false;
            setStatus('success', 'Movies loaded successfully');
            loadingContainer.style.display = 'none';
            displayMovies(movies);
            startBtn.disabled = false;
        } else if (data.status === 'fetching' || data.status === 'loading') {
            // Poll for updates
            await pollForMovies();
        } else {
            setStatus('error', 'Failed to fetch movies');
            loadingContainer.style.display = 'none';
            startBtn.disabled = false;
        }
    } catch (error) {
        console.error('Error fetching movies:', error);
        setStatus('error', 'Network error occurred');
        loadingContainer.style.display = 'none';
        startBtn.disabled = false;
    } finally {
        isFetching = false;
    }
}

async function pollForMovies() {
    let attempts = 0;
    const maxAttempts = 60; // 60 seconds timeout
    
    while (attempts < maxAttempts) {
        try {
            const response = await fetch('/api/status');
            const data = await response.json();
            
            if (!data.is_loading) {
                // Loading complete, get movies
                const moviesResponse = await fetch('/api/movies');
                const moviesData = await moviesResponse.json();
                
                if (moviesData.movies && moviesData.movies.length > 0) {
                    movies = moviesData.movies;
                    updateMovieCount(movies.length);
                    viewBtn.disabled = false;
                    setStatus('success', 'Movies loaded successfully');
                    loadingContainer.style.display = 'none';
                    displayMovies(movies);
                    startBtn.disabled = false;
                    return;
                }
            }
            
            // Wait 1 second before next poll
            await new Promise(resolve => setTimeout(resolve, 1000));
            attempts++;
        } catch (error) {
            console.error('Polling error:', error);
            break;
        }
    }
    
    // Timeout or error
    setStatus('error', 'Timeout loading movies');
    loadingContainer.style.display = 'none';
    startBtn.disabled = false;
}

async function loadMovies() {
    try {
        const response = await fetch('/api/movies');
        const data = await response.json();
        
        if (data.movies && data.movies.length > 0) {
            movies = data.movies;
            updateMovieCount(movies.length);
            displayMovies(movies);
            if (searchResultsContainer) {
                searchResultsContainer.style.display = 'none';
            }
        } else {
            setStatus('error', 'No movies available');
        }
    } catch (error) {
        console.error('Error loading movies:', error);
        setStatus('error', 'Failed to load movies');
    }
}

async function getMovieDetails(index) {
    if (selectedIndex === index) {
        // If same movie, just open modal
        openModal(index);
        return;
    }
    
    selectedIndex = index;
    openModal(index);
}

async function openModal(index) {
    try {
        const response = await fetch(`/api/movie/${index}`);
        const data = await response.json();
        
        if (data.error) {
            modalTitle.textContent = 'Error';
            modalBody.innerHTML = `<p class="detail-error">${data.error}</p>`;
        } else {
            modalTitle.textContent = data.title || 'Movie Details';
            if (data.from_search) {
                modalTitle.textContent += ' 🔍 (Search Result)';
            }
            renderDetails(data.details);
        }
        
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    } catch (error) {
        console.error('Error fetching movie details:', error);
        modalTitle.textContent = 'Error';
        modalBody.innerHTML = '<p class="detail-error">Failed to load movie details</p>';
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeModal() {
    modal.classList.remove('active');
    document.body.style.overflow = 'auto';
}

async function refreshCache() {
    try {
        setStatus('loading', 'Refreshing cache...');
        const response = await fetch('/api/refresh');
        const data = await response.json();
        
        if (data.status === 'success') {
            movies = [];
            searchResults = [];
            updateMovieCount(0);
            viewBtn.disabled = true;
            movieListContainer.style.display = 'none';
            if (searchResultsContainer) {
                searchResultsContainer.style.display = 'none';
            }
            setStatus('success', 'Cache cleared. Click Start to fetch movies.');
        }
    } catch (error) {
        console.error('Error refreshing cache:', error);
        setStatus('error', 'Failed to refresh cache');
    }
}

async function checkStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        if (data.is_loading) {
            setStatus('loading', 'Movies are being fetched...');
            startBtn.disabled = true;
            viewBtn.disabled = true;
            loadingContainer.style.display = 'block';
            
            // Poll for completion
            await pollForMovies();
        } else if (data.movies_count > 0) {
            movies = await fetch('/api/movies').then(r => r.json()).then(d => d.movies);
            updateMovieCount(movies.length);
            viewBtn.disabled = false;
            setStatus('success', 'Ready');
        }
    } catch (error) {
        console.error('Status check error:', error);
    }
}

// ============================================================
// UI HELPERS
// ============================================================

function setStatus(type, text) {
    statusDot.className = 'status-dot';
    if (type === 'loading') {
        statusDot.classList.add('loading');
    } else if (type === 'error') {
        statusDot.classList.add('error');
    }
    statusText.textContent = text;
}

function updateMovieCount(count) {
    movieCount.textContent = `${count} movies loaded`;
}

function displayMovies(movies) {
    if (!movies || movies.length === 0) {
        movieListContainer.style.display = 'none';
        return;
    }
    
    movieListContainer.style.display = 'block';
    movieTotal.textContent = `${movies.length} movies`;
    
    movieGrid.innerHTML = movies.map((title, index) => `
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
    
    modalBody.innerHTML = details.map((detail, index) => {
        let badgeType = '';
        let badgeText = '';
        
        if (detail.type === 'magnet') {
            badgeType = 'magnet';
            badgeText = '🧲 Magnet';
        } else if (detail.type === 'none') {
            badgeType = 'none';
            badgeText = '⚠️ No Link';
        } else if (detail.type === 'error') {
            badgeType = 'error';
            badgeText = '❌ Error';
        }
        
        let content = '';
        
        if (detail.type === 'magnet') {
            // Display screenshot if available
            let screenshotHtml = '';
            if (detail.screenshot) {
                screenshotHtml = `
                    <div class="detail-screenshot">
                        <img src="${escapeHtml(detail.screenshot)}" alt="${escapeHtml(detail.title)}" 
                             onerror="this.style.display='none'">
                    </div>
                `;
            }
            
            // Display all screenshots
            let screenshotsHtml = '';
            if (detail.screenshots && detail.screenshots.length > 0) {
                screenshotsHtml = `
                    <div class="detail-screenshots">
                        ${detail.screenshots.map(src => `
                            <img src="${escapeHtml(src)}" alt="Screenshot" 
                                 onerror="this.style.display='none'">
                        `).join('')}
                    </div>
                `;
            }
            
            content = `
                <div class="detail-title">🎬 ${escapeHtml(detail.title)}</div>
                <div class="detail-type-badge ${badgeType}">${badgeText}</div>
                ${detail.quality ? `<div class="detail-quality">📺 Quality: ${escapeHtml(detail.quality)}</div>` : ''}
                ${screenshotHtml}
                ${screenshotsHtml}
                <div class="detail-magnet">${escapeHtml(detail.magnet)}</div>
                <button class="detail-copy-btn" onclick="copyToClipboard('${escapeHtml(detail.magnet)}')">📋 Copy Magnet</button>
            `;
        } else if (detail.type === 'none' || detail.type === 'error') {
            // Display screenshot even if no magnet
            let screenshotHtml = '';
            if (detail.screenshot) {
                screenshotHtml = `
                    <div class="detail-screenshot">
                        <img src="${escapeHtml(detail.screenshot)}" alt="${escapeHtml(detail.title)}" 
                             onerror="this.style.display='none'">
                    </div>
                `;
            }
            
            content = `
                <div class="detail-title">🎬 ${escapeHtml(detail.title)}</div>
                <div class="detail-type-badge ${badgeType}">${badgeText}</div>
                ${screenshotHtml}
                <p class="${detail.type === 'error' ? 'detail-error' : 'detail-empty'}">${escapeHtml(detail.message || 'No links available')}</p>
            `;
        }
        
        return `<div class="detail-item">${content}</div>`;
    }).join('');
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        const btn = event.target;
        btn.textContent = '✅ Copied!';
        setTimeout(() => {
            btn.textContent = '📋 Copy Magnet';
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        // Fallback
        const input = document.createElement('input');
        input.value = text;
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        const btn = event.target;
        btn.textContent = '✅ Copied!';
        setTimeout(() => {
            btn.textContent = '📋 Copy Magnet';
        }, 2000);
    });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ============================================================
// KEYBOARD SHORTCUTS
// ============================================================

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
    }
});

// Close modal when clicking outside
modal.addEventListener('click', (e) => {
    if (e.target === modal) {
        closeModal();
    }
});