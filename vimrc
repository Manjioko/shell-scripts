" 基础设置
    set nocompatible
    filetype plugin indent on
    syntax on
    set encoding=utf-8
    set number
    " set relativenumber
    set cursorline
    set showcmd
    set wildmenu
    set autoindent
    set smartindent
    set expandtab
    set tabstop=4
    set softtabstop=4
    set shiftwidth=4
    set hlsearch
    set incsearch
    set ignorecase
    set smartcase
    set scrolloff=5
    set history=1000
    set showmatch
    set mouse=a
    set backspace=indent,eol,start
    let mapleader = " "

    " 自动安装 vim-plug
    let data_dir = has('nvim') ? stdpath('data') . '/site' : '~/.vim'
    if empty(glob(data_dir . '/autoload/plug.vim'))
      silent execute '!curl -fLo '.data_dir.'/autoload/plug.vim --create-dirs
  https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'
      autocmd VimEnter * PlugInstall --sync | source $MYVIMRC
    endif

    call plug#begin('~/.vim/plugged')

    " 外观/主题
    " Plug 'morhetz/gruvbox'
    Plug 'joshdick/onedark.vim'
    " Git 相关
    Plug 'tpope/vim-fugitive'
    Plug 'tpope/vim-rhubarb'
    Plug 'airblade/vim-gitgutter'

    " 状态栏
    Plug 'vim-airline/vim-airline'
    Plug 'vim-airline/vim-airline-themes'

    " 缓冲区管理
    Plug 'ap/vim-buftabline'
    Plug 'jlanzarotta/bufexplorer'

    " 自动补全（需 Node.js）
    Plug 'neoclide/coc.nvim', {'branch': 'release'}

    " 文件/内容查找
    Plug 'junegunn/fzf', { 'do': { -> fzf#install() } }
    Plug 'junegunn/fzf.vim'

    " 其他常用
    Plug 'preservim/nerdtree'
    Plug 'tpope/vim-commentary'
    Plug 'tpope/vim-surround'
    Plug 'jiangmiao/auto-pairs'
    Plug 'Yggdroot/indentLine'

    call plug#end()

    " 主题与界面
    set termguicolors
    colorscheme onedark
    set background=dark

    " 状态栏配置
    let g:airline#extensions#tabline#enabled = 1
    let g:airline#extensions#tabline#formatter = 'unique_tail'
    "let g:airline_powerline_fonts = 1
    let g:airline_theme = 'onedark'
    let g:airline#extensions#branch#enabled = 0

    " Git Gutter
    set signcolumn=yes
    let g:gitgutter_max_signs = 500

    " 缓冲区快捷键
    nnoremap <C-n> :bnext<CR>
    nnoremap <C-p> :bprevious<CR>
    nnoremap <C-d> :bdelete<CR>
    nnoremap <leader>be :BufExplorer<CR>
    let g:buftabline_show = 1
    let g:buftabline_indicators = 1
    let g:gitgutter_async = 1              " 异步运行 git diff（需要 Vim 8.0+）
    let g:gitgutter_eager = 0              " 关闭“输入时立即更新”，改为光标静止后更新
    set updatetime=250                     " 缩短 CursorHold 触发时间（默认 4000ms）
    set showtabline=2       " 强制常显标签栏

    " NERDTree
    nnoremap <leader>n :NERDTreeToggle<CR>
    let NERDTreeShowHidden = 1
    let NERDTreeMinimalUI = 1
    let NERDTreeAutoDeleteBuffer = 1

    " fzf
    nnoremap <C-f> :Files<CR>
    nnoremap <C-g> :GFiles?<CR>
    let g:fzf_layout = { 'window': { 'width': 0.9, 'height': 0.6 } }

    " coc.nvim 自动补全 Tab 映射
    inoremap <silent><expr> <TAB>
          \ coc#pum#visible() ? coc#pum#next(1) :
          \ CheckBackspace() ? "\<Tab>" :
          \ coc#refresh()
    inoremap <expr><S-TAB> coc#pum#visible() ? coc#pum#prev(1) : "\<C-h>"
    inoremap <silent><expr> <CR> coc#pum#visible() ? coc#pum#confirm() : "\<C-g>u\<CR>"

    function! CheckBackspace() abort
      let col = col('.') - 1
      return !col || getline('.')[col - 1]  =~# '\s'
    endfunction

    " coc 代码跳转
    nmap <silent> gd <Plug>(coc-definition)
    nmap <silent> gy <Plug>(coc-type-definition)
    nmap <silent> gi <Plug>(coc-implementation)
    nmap <silent> gr <Plug>(coc-references)
    nnoremap <silent> K :call CocActionAsync('doHover')<CR>

    " 通用快捷键
    nnoremap <leader>w :w<CR>
    nnoremap <leader>q :q<CR>
    nnoremap <leader>h :nohlsearch<CR>
    nnoremap <leader>v <C-w>v
    nnoremap <leader>s <C-w>s
    nnoremap <leader>wh <C-w>h
    nnoremap <leader>wj <C-w>j
    nnoremap <leader>wk <C-w>k
    nnoremap <leader>wl <C-w>l
    nmap <C-/> gcc
    vmap <C-/> gc

    " 自动命令
    autocmd BufWritePre * %s/\s\+$//e
    " 静默恢复上次光标位置，避免 E20 错误
    autocmd BufReadPost * if &ft !~# 'commit' && line("'\"") > 0 &&  line("'\"") <= line("$") | execute "silent! normal! g`\"" | endif
