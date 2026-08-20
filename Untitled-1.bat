cd "C:\Users\Administrator\Desktop\video-rewash-tool-main"

# 配置 Git 用户信息（只需要第一次执行）
git config --global user.name "baideji521"
git config --global user.email "599256551@qq.com"

# 确认 Git 身份
Write-Host "========================================"
Write-Host "Git 用户信息"
Write-Host "========================================"
Write-Host "用户名：" (git config --global user.name)
Write-Host "邮箱："   (git config --global user.email)
Write-Host ""

# 初始化 Git（已经存在也不会有问题）
git init

# 添加全部文件
Write-Host "========================================"
Write-Host "添加文件"
Write-Host "========================================"
git add .

# 查看待提交文件
Write-Host "========================================"
Write-Host "Git 状态"
Write-Host "========================================"
git status

# 提交
Write-Host "========================================"
Write-Host "开始提交"
Write-Host "========================================"
git commit -m "Initial commit"

Write-Host ""
Write-Host "========================================"
Write-Host "完成"
Write-Host "========================================"