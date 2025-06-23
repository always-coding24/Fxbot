# Fxbot
# Step 1: Update packages and install git & python
pkg update && pkg upgrade -y
pkg install git python -y

# Step 2: Clone the repository
git clone https://github.com/always-coding24/Fxbot.git
cd Fxbot

# Step 3: Install required Python libraries
pip install requests colorama