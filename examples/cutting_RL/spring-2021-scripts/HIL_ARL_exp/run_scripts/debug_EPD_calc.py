import argparse

import torch
import gpytorch
from matplotlib import cm
import os
import sys
sys.path.append('/home/sony/Documents/frankapy/examples/cutting_RL/spring-2021-scripts/HIL_ARL_exp/')

from reward_learner import RewardLearner
from policy_learner import REPSPolicyLearner
from gp_regression_model import GPRegressionModel
from data_utils import *


import subprocess
import numpy as np
import matplotlib.pyplot as plt
plt.close("all")
import math
import rospy
import argparse
import pickle
from autolab_core import RigidTransform, Point
from frankapy import FrankaArm
from cv_bridge import CvBridge

from frankapy import FrankaArm, SensorDataMessageType
from frankapy import FrankaConstants as FC
from frankapy.proto_utils import sensor_proto2ros_msg, make_sensor_group_msg
from frankapy.proto import ForcePositionSensorMessage, ForcePositionControllerSensorMessage, ShouldTerminateSensorMessage
from franka_interface_msgs.msg import SensorDataGroup
from frankapy.utils import *

from tqdm import trange
from rl_utils import reps
import time 
import copy

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--use_all_dmp_dims', type=bool, default = False)
    parser.add_argument('--dmp_traject_time', '-t', type=int, default = 5)  
    parser.add_argument('--dmp_wt_sampling_var', type=float, default = 0.01)
    parser.add_argument('--num_epochs', '-e', type=int, default = 5)  
    parser.add_argument('--num_samples', '-s', type=int, default = 25)    
    parser.add_argument('--data_savedir', '-d', type=str, default='/home/sony/Documents/cutting_RL_experiments/data/Jan-2021-HIL-ARL-exps/')
    parser.add_argument('--exp_num', '-n', type=int)
    parser.add_argument('--food_type', type=str, default='hard') #hard or soft
    parser.add_argument('--food_name', type=str, help = 'potato, carrot, celery, tomator, banana, mozz') #potato, carrot, celery, tomato, banana, mozz
    parser.add_argument('--cut_type', type=str, default = 'normal', help ='options: normal, pivchop, scoring') # options: 'normal', 'pivchop', 'scoring'
    parser.add_argument('--start_from_previous', '-sfp', type=bool, default=False)
    parser.add_argument('--previous_datadir', '-pd', type=str)
    parser.add_argument('--prev_epochs_to_calc_pol_update', '-num_prev_epochs', type=int, default = 1, help='num \
        previous epochs of data to use to calculate REPS policy update')
    parser.add_argument('--starting_epoch_num', '-start_epoch', type=int, default = 0)
    parser.add_argument('--starting_sample_num', '-start_sample', type=int, default = 0)
    parser.add_argument('--debug', type=bool, default=False)
    
    # GP reward model-related args
    parser.add_argument('--kappa', type=int, default = 5)
    parser.add_argument('--rel_entropy_bound', type=float, default = 1.2)
    parser.add_argument('--num_EPD_epochs', type=int, default = 5)
    parser.add_argument('--GP_training_epochs_initial', type=int, default = 120)
    parser.add_argument('--GP_training_epochs_later', type=int, default = 11)
    parser.add_argument('--desired_cutting_behavior', type=str, help='options: fast, slow, quality_cut') # fast, slow, quality_cut
    parser.add_argument('--standardize_reward_feats', type=bool, default = False)
    args = parser.parse_args()

    kappa = args.kappa   
    initial_var = args.dmp_wt_sampling_var   
    num_episodes_initial_epoch = args.num_samples     
    num_episodes_later_epochs = args.num_samples
    rel_entropy_bound = args.rel_entropy_bound
    num_EPD_epochs = args.num_EPD_epochs
    GP_training_epochs_initial = args.GP_training_epochs_initial
    GP_training_epochs_later = args.GP_training_epochs_later    

    if args.desired_cutting_behavior == 'fast' or args.desired_cutting_behavior == 'slow':
        num_expert_rews_each_sample = 2
    elif args.desired_cutting_behavior == 'quality_cut':
        num_expert_rews_each_sample = 1

    # Instantiate Policy Learner (agent)
    agent = REPSPolicyLearner()

    # Instantiate reward learner - note: GPR model not instantiated yet
    reward_learner = RewardLearner(kappa)
    beta = 0.001 # fixed gaussian noise likelihood
    
    # create folders to save data
    if not os.path.isdir(args.data_savedir + args.cut_type + '/' + args.food_name + '/'):
        createFolder(args.data_savedir + args.cut_type + '/' + args.food_name + '/')
    args.data_savedir = args.data_savedir + args.cut_type + '/' + args.food_name + '/'
    if not os.path.isdir(args.data_savedir + 'exp_' + str(args.exp_num)):
        createFolder(args.data_savedir + 'exp_' + str(args.exp_num))

    work_dir = args.data_savedir + 'exp_' + str(args.exp_num)
    
    if not os.path.isdir(work_dir + '/' + 'all_polParamRew_data'):
        createFolder(work_dir + '/' + 'all_polParamRew_data')
    if not os.path.isdir(work_dir + '/' + 'GP_reward_model_data'):
        createFolder(work_dir + '/' + 'GP_reward_model_data')
    if not os.path.isdir(work_dir + '/' + 'GP_reward_model_data' + '/' + 'GP_cov_mat'):
        createFolder(work_dir + '/' + 'GP_reward_model_data' + '/' + 'GP_cov_mat')
    if not os.path.isdir(work_dir + '/' + 'dmp_traject_plots'):
        createFolder(work_dir + '/' + 'dmp_traject_plots')    
    if not os.path.isdir(work_dir + '/' + 'dmp_wts'):
        createFolder(work_dir + '/' + 'dmp_wts')
    if not os.path.isdir(work_dir + '/' + 'forces_positions'):
        createFolder(work_dir + '/' + 'forces_positions')
 
    # load dmp weights and starting knife orientation
    dmp_wts_file, knife_orientation = load_dmp_wts_and_knife_orientation(args.cut_type)
    
    position_dmp_pkl = open(dmp_wts_file,"rb")
    init_dmp_info_dict = pickle.load(position_dmp_pkl)

    # print('Starting robot')
    # fa = FrankaArm()
    
    # if not args.debug:
    #     reset_joint_positions = [ 0.02846037, -0.51649966, -0.12048514, -2.86642333, -0.05060268,  2.30209197, 0.7744833 ]
    #     fa.goto_joints(reset_joint_positions)    

    # # go to initial cutting pose
    # starting_position = RigidTransform(rotation=knife_orientation, \
    #     translation=np.array([0.48, 0.044, 0.09]), #z=0.05
    #     from_frame='franka_tool', to_frame='world')    
    # fa.goto_pose(starting_position, duration=5, use_impedance=False)

    # # move down to contact
    # if not args.debug:
    #     move_down_to_contact = RigidTransform(translation=np.array([0.0, 0.0, -0.1]),
    #     from_frame='world', to_frame='world')   
    #     if args.food_name == 'banana': 
    #         fa.goto_pose_delta(move_down_to_contact, duration=5, use_impedance=False, force_thresholds=[10.0, 10.0, 2.0, 10.0, 10.0, 10.0], ignore_virtual_walls=True)
    #     else:
    #         fa.goto_pose_delta(move_down_to_contact, duration=5, use_impedance=False, force_thresholds=[10.0, 10.0, 3.0, 10.0, 10.0, 10.0], ignore_virtual_walls=True)    
               
    # Initialize Gaussian policy params (DMP weights) - mean and sigma
    initial_wts, initial_mu, initial_sigma, S, control_type_z_axis = agent.initialize_gaussian_policy(num_expert_rews_each_sample, args.cut_type, args.food_type, args.dmp_wt_sampling_var, args.start_from_previous, \
        args.previous_datadir, args.prev_epochs_to_calc_pol_update, init_dmp_info_dict, work_dir, dmp_wts_file, args.starting_epoch_num, args.dmp_traject_time)
    print('initial mu', initial_mu)        
    mu, sigma = initial_mu, initial_sigma

    # import pdb; pdb.set_trace()

    mean_params_each_epoch, cov_each_epoch = [], []
    # if starting from a later epoch: load previous data    
    if os.path.isfile(os.path.join(work_dir, 'policy_mean_each_epoch.npy')):
        mean_params_each_epoch = np.load(os.path.join(work_dir, 'policy_mean_each_epoch.npy')).tolist()
        cov_each_epoch = np.load(os.path.join(work_dir, 'policy_cov_each_epoch.npy')).tolist()
    else:   
        mean_params_each_epoch.append(initial_mu)   
        cov_each_epoch.append(initial_sigma) 
    
    # Buffers for GP reward model data
    total_queried_samples_each_epoch, mean_reward_model_rewards_all_epochs = [], [] #track number of queries to expert for rewards and average rewards for each epoch
    training_data_list, queried_samples_all = [], []
    GP_training_data_x_all = np.empty([0,7])
    GP_training_data_y_all, GP_training_data_y_all_slow, GP_training_data_y_all_fast =  np.empty([0]), np.empty([0]), np.empty([0]) 
    queried_outcomes, queried_expert_rewards, policy_params_all_epochs, block_poses_all_epochs = [], [], [], []
    reward_model_rewards_all_mean_buffer, expert_rewards_all_epochs = [], [] # TODO: update expert_rewards_all_epochs to load previous data 
    total_samples = 0
    
    # Track success metrics
    '''
    task_success: 0 (unsuccessful cut), 1 (average cut), 2 (good cut)
    task_success_more_granular: cut through (0/1), cut through except for small tag (0/1), housing bumped into food/pushed out of gripper (0/1)
    '''
    time_to_complete_cut, task_success, task_success_more_granular = [], [], []    
    # save reward features (for easier data post-processing)
    reward_features_all_samples = []
    # load previous data in starting from later sample/epoch
    if args.starting_sample_num !=0 or args.starting_epoch_num!=0:
        reward_features_all_samples, time_to_complete_cut, task_success, task_success_more_granular = load_prev_task_success_data(work_dir)        
        
        # load prev GP training data list
        prev_GP_training_data_list = np.load(work_dir + '/' + 'GP_reward_model_data/' + 'training_data_list.npy', allow_pickle=True)
        training_data_list = prev_GP_training_data_list.tolist()

        prev_expert_rewards_all_epochs = np.load(work_dir + '/' + 'GP_reward_model_data/' + 'expert_rewards_all_epochs.npy')     
        expert_rewards_all_epochs = prev_expert_rewards_all_epochs.tolist()

        # load prev queried_samples_all
        if args.desired_cutting_behavior == 'slow':
            prev_queried_samples_all = np.load(work_dir + '/' + 'GP_reward_model_data/' + 'queried_samples_all_slowCut.npy')
            queried_samples_all = prev_queried_samples_all.tolist()

            prev_total_queried_samples_each_epoch = np.load(work_dir + '/' 'GP_reward_model_data/' + 'total_queried_samples_each_epoch_slowCut.npy')
            total_queried_samples_each_epoch = prev_total_queried_samples_each_epoch.tolist()    

        elif args.desired_cutting_behavior == 'fast':
            prev_queried_samples_all = np.load(work_dir + '/' + 'GP_reward_model_data/' + 'queried_samples_all_fastCut.npy')
            queried_samples_all = prev_queried_samples_all.tolist()

            prev_total_queried_samples_each_epoch = np.load(work_dir + '/' 'GP_reward_model_data/' + 'total_queried_samples_each_epoch_fastCut.npy')
            total_queried_samples_each_epoch = prev_total_queried_samples_each_epoch.tolist()    

        else:    
            prev_queried_samples_all = np.load(work_dir + '/' + 'GP_reward_model_data/' + 'queried_samples_all_qualityCut.npy')
            queried_samples_all = prev_queried_samples_all.tolist()

            prev_total_queried_samples_each_epoch = np.load(work_dir + '/' 'GP_reward_model_data/' + 'total_queried_samples_each_epoch_qualityCut.npy')
            total_queried_samples_each_epoch = prev_total_queried_samples_each_epoch.tolist()    
        
        import pdb; pdb.set_trace()
    
    # if not starting from 0-th epoch - need to load/train GP reward model based on previous data
    # TODO: pull this out to a separate function
    if args.starting_epoch_num > 0:
        print('training GP reward model from previously saved data')
        # import pdb; pdb.set_trace()    
            
        if args.desired_cutting_behavior == 'slow':
            prev_GP_training_data = np.load(work_dir + '/' 'GP_reward_model_data/' + 'GP_reward_model_training_data_slowCut_epoch_' +str(args.starting_epoch_num-1) + '.npz')
            GP_training_data_x_all = prev_GP_training_data['GP_training_data_x_all']
            GP_training_data_y_all = prev_GP_training_data['GP_training_data_y_all']
            GP_training_data_y_all_slow = GP_training_data_y_all

            # plot histogram of standardized reward featuress
            # for i in range(GP_training_data_x_all.shape[0]):
            #     plt.plot(GP_training_data_x_all[i,:],'o-')
            # plt.xlabel('reward feature dimension num')
            # plt.ylabel('reward feature value - standardized features')
            # plt.title('distrib of reward feature values for each dimension - standardized features')
            # plt.show()

            # fig, axs = plt.subplots(1, 7, sharey=True, tight_layout=True)
            # for dim in range(7):
            #     axs[dim].hist(GP_training_data_x_all[:,dim], bins='auto')
            #     axs[dim].set_title('reward feat %i - standardized' %dim)
            #     axs[dim].set_xlabel('reward feat dim %i value' %dim)
            #     axs[dim].set_ylabel('reward feat dim %i frequency' %dim)            
            # plt.show()

        elif args.desired_cutting_behavior == 'fast': 
            prev_GP_training_data = np.load(work_dir + '/' 'GP_reward_model_data/' + 'GP_reward_model_training_data_fastCut_epoch_' +str(args.starting_epoch_num-1) + '.npz')
            GP_training_data_x_all = prev_GP_training_data['GP_training_data_x_all']
            GP_training_data_y_all = prev_GP_training_data['GP_training_data_y_all']
            GP_training_data_y_all_fast = GP_training_data_y_all
        
        else:
            prev_GP_training_data = np.load(work_dir + '/' 'GP_reward_model_data/' + 'GP_reward_model_training_data_qualityCut_epoch_' +str(args.starting_epoch_num-1) + '.npz')
            GP_training_data_x_all = prev_GP_training_data['GP_training_data_x_all']
            GP_training_data_y_all = prev_GP_training_data['GP_training_data_y_all']

        # train with previous data
        print('initializing and training GP reward model from previous data')
        likelihood = gpytorch.likelihoods.GaussianLikelihood()      
        # import pdb; pdb.set_trace()      
        train_x = torch.from_numpy(GP_training_data_x_all)
        train_x = train_x.float()
        train_y = torch.from_numpy(GP_training_data_y_all)
        train_y = train_y.float()
        print('train_y variance', train_y.var())
        # add white noise 
        #likelihood = gpytorch.likelihoods.FixedNoiseGaussianLikelihood(torch.ones(train_x.shape[0]) * beta)
        gpr_reward_model = GPRegressionModel(train_x, train_y, likelihood) 
        optimizer = torch.optim.Adam([                
            {'params': gpr_reward_model.covar_module.parameters()},
            {'params': gpr_reward_model.mean_module.parameters()},
            {'params': gpr_reward_model.likelihood.parameters()},
        ], lr=0.01) # lr = 0.01 originally 
        mll = gpytorch.mlls.ExactMarginalLogLikelihood(likelihood, gpr_reward_model)
        # train reward GP model given initial train_x and train_y data (voxel_data, queried human rewards)
        gpr_reward_model = reward_learner.train_GPmodel(work_dir, GP_training_epochs_initial, optimizer, gpr_reward_model, likelihood, mll, train_x, train_y)
    
    # import pdb; pdb.set_trace()

    pi_current_mean = mu
    pi_current_cov = sigma

    pi_tilda_mean = np.load(os.path.join(work_dir, 'REPSupdatedMean_' + 'epoch_1.npz'))['updated_mean']
    pi_tilda_cov = np.load(os.path.join(work_dir, 'REPSupdatedMean_' + 'epoch_1.npz'))['updated_cov']
    # pi_tilda_wts = reps_wts
    
    # determine outcomes to query using PI
    samples_to_query, queried_outcomes  = reward_learner.calc_PI_all_outcomes(training_data_list, queried_samples_all, lambda_thresh=1.0, eps=0.01, beta = 0.5)
    import pdb; pdb.set_trace()

    # # determine outcomes to query using EPD
    # current_epoch = args.starting_epoch_num
    # samples_to_query, queried_outcomes  = reward_learner.compute_EPD_for_each_sample_updated(current_epoch, args.num_samples, work_dir, num_EPD_epochs, optimizer, \
    #     gpr_reward_model, likelihood, mll, agent, pi_tilda_mean, pi_tilda_cov, pi_current_mean, pi_current_cov, \
    #         training_data_list, queried_samples_all, GP_training_data_x_all, GP_training_data_y_all, beta, initial_wts, args.cut_type, S) 

    import pdb; pdb.set_trace()