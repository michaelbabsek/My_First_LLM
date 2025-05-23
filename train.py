import contextlib
import os
import time

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import tqdm
from transformers import get_cosine_schedule_with_warmup

from dataset import BinDataset
from model import Transformer, ModelArgs
from tokenizer import Tokenizer

#model args
n_dim: int = 768
n_blocks: int = 16
n_heads: int = 8
max_seq_len: int = 1024

# training
train_iters = 60_000
eval_iters = 2
eval_interval = 1000
warmup_frac = 0.1
batch_size: int = 1
max_lr = 6e-4
warmup_steps_percentage = 0.1

#generation
max_token_len = 100

def get_device():
    if torch.cuda.is_available():
        return "cuda"
    elif torch.backends.mps.is_available():
        return "mps"
    else:
        return "cpu"

device = get_device()

cuda = device == "cuda"
ctx = torch.cuda.amp.autocast() if cuda else contextlib.nullcontext()
scaler = torch.cuda.amp.GradScaler() if cuda else None

tokenizer = Tokenizer()

model = Transformer(
    args=ModelArgs(
    n_dim=n_dim,
    n_blocks=n_blocks,
    n_heads=n_heads,
    max_seq_len=max_seq_len,
    vocab_size=len(tokenizer))).to(device)

if torch.__version__ >= "2.0" and device == "cuda":
    model.compile()

if os.path.exists('./model.pt'): # load model if existing
    model.load_state_dict(torch.load("model.pt", map_location=device))

optimizer = torch.optim.AdamW(model.parameters(), lr=max_lr)

scheduler = get_cosine_schedule_with_warmup(
    optimizer=optimizer,
    num_warmup_steps=train_iters * warmup_frac,
    num_training_steps=train_iters, num_cycles=0.5
)

train_data = BinDataset(chunk_size=max_seq_len, split="train", device=device)
val_data = BinDataset(chunk_size=max_seq_len, split="val", device=device)

train_loader = DataLoader(dataset=train_data, batch_size=batch_size, shuffle=False, pin_memory=cuda) #shuffling would take ages
val_loader = DataLoader(dataset=val_data, batch_size=batch_size, shuffle=False, pin_memory=cuda)

def train():
    torch.set_float32_matmul_precision('high')
    model.train()
    pbar = tqdm(total=train_iters, desc="Training step")
    loss_sum = 0.0

    train_iter = iter(train_loader)
    for step_idx in range(train_iters):
        x, y = next(train_iter)

        with ctx:
            loss, _ = model(x, y)

            optimizer.zero_grad()

            if cuda:
                scaler.scale(loss).backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()

            scheduler.step()

            step_loss = loss.item()
            loss_sum += step_loss
            avg_loss = loss_sum / (step_idx + 1)

            pbar.update(1)
            pbar.set_postfix(step_loss=f"{step_loss:.4f}",
                             avg_loss=f"{avg_loss:.4f}")

    pbar.close()

if __name__ == "__main__":
    train()